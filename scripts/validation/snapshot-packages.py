"""Build every deployable package from a synthetic fixture and fingerprint it.

Purpose
-------
Proves that a code change alters the packages AWS receives, and the scores a
trained model produces, only where expected (first used for the stage
reorganisation; see
``planning/evidence/package-reorganisation-local-validation-20261025.json``).
No AWS calls, no real data, no network.

Steps:

1. Build the same 320-row synthetic dataset as ``tests/test_ingestion.py``,
   run the baseline stage and train a tiny 3-round model on it.
2. Run every packaging script into ``--output``: the Glue/control bundle,
   the acquisition Lambda context, both dashboard Lambda zips, the SageMaker
   and streaming container contexts and the cloud-scoring parity package.
3. Write ``snapshot.json``: for every package, each member file's SHA-256
   (zip members are expanded), plus the whole-zip hashes.
4. With ``--reference <earlier snapshot dir>``, re-score that snapshot's
   fixture rows with its saved model and the *current* code, then report
   added/removed/changed package members and whether scores, explained
   queue and the retrained model are identical.

Usage::

    python scripts/validation/snapshot-packages.py --output output/reorg-baseline/snapshot
    python scripts/validation/snapshot-packages.py --output output/reorg-phase1/snapshot \\
        --reference output/reorg-baseline/snapshot
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from bda_pipeline.ingestion.acquisition import HEADER, prepare_inputs
from bda_pipeline.features.behaviour import add_behaviour
from bda_pipeline.scoring.inference import prepare_inference
from bda_pipeline.control.ingest_compute import baseline_stage
from bda_pipeline.modeling.model import compare_models
from bda_pipeline.ingestion.transform import build_curated_transactions, read_accounts, read_transactions

# Files that embed (or hash) the model's generation timestamp, so they differ on
# every run even when nothing changed. Reported separately as metadata.
METADATA = {'comparison.json', 'selection.json', 'model-manifest.json', 'package.json',
            'build-context.json', 'artifact-hashes.json', 'deployment.json', 'terraform.tfvars.json'}
IMAGE_URI = '588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@sha256:' + 'a' * 64


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def load_script(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem.replace('-', '_'), ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_main(module, argv):
    """Call a script's argparse ``main()`` with the given arguments."""
    saved = sys.argv
    sys.argv = ['snapshot', *argv]
    try:
        module.main()
    finally:
        sys.argv = saved


def build_fixture(work):
    """Synthetic raw data -> curated -> baseline stage, as in tests/test_ingestion.py."""
    raw = work / 'dataset'
    raw.mkdir(parents=True)
    with (raw / 'HI-Small_Trans.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        for i in range(320):
            day = [1, 7, 9, 12][i // 80]
            writer.writerow([f'2022/09/{day:02d} 12:00', '001', '000A', '1', '000A', i + 1,
                             'US Dollar', i + 1, 'US Dollar', 'ACH' if i % 2 == 0 else 'Wire', int(i % 7 == 0)])
    (raw / 'HI-Small_accounts.csv').write_text('Bank Name,Bank ID,Account Number,Entity ID,Entity Name\nBank,1,000A,E1,Fixture\n')
    rates = pd.DataFrame([dict(txn_date=str(day.date()), currency='US Dollar', usd_rate=1., rate_date=str(day.date()), source='fixture')
                          for day in pd.date_range('2022-08-31', '2022-09-12')])
    rates.to_csv(raw / 'fx.csv', index=False)
    inputs = work / 'input'
    info = prepare_inputs(raw / 'HI-Small_Trans.csv', raw / 'HI-Small_accounts.csv', inputs / 'raw', fx_file=raw / 'fx.csv')
    frame = build_curated_transactions(read_transactions(inputs / 'raw/transactions.csv'),
                                       read_accounts(inputs / 'raw/accounts.csv'), rates)
    frame['transaction_id'] = 'HI-Small:' + frame.source_row_index.astype(str)
    frame['amount_usd_asof'] = frame.amount_usd
    frame = add_behaviour(frame)
    (inputs / 'curated').mkdir()
    frame.to_parquet(inputs / 'curated/data.parquet', index=False)
    (inputs / 'run').mkdir()
    (inputs / 'model').mkdir()
    (inputs / 'model/deployment.json').write_text(json.dumps({'image_uri': IMAGE_URI}))
    (inputs / 'run/manifest.json').write_text(json.dumps(info | {'run_id': 'snapshot', 'source_scope': 'synthetic-fixture'}))
    baseline_stage(inputs, work / 'baseline', ROOT / 'dashboard')
    return work / 'baseline/baseline/bq1_scored.parquet', work / 'baseline/manifest/dashboard.json'


def fingerprint(path):
    """Map every file (and every member of every zip) under ``path`` to its SHA-256."""
    result = {}
    for file in sorted(p for p in path.rglob('*') if p.is_file()):
        name = file.relative_to(path).as_posix()
        if '__pycache__' in name:
            continue
        data = file.read_bytes()
        result[name] = sha256(data)
        if file.suffix == '.zip':
            with zipfile.ZipFile(file) as archive:
                for member in sorted(archive.namelist()):
                    result[f'{name}!{member}'] = sha256(archive.read(member))
    return result


def build_packages(output, model, scored):
    packages = output / 'packages'
    prepare_inference(model, scored, packages / 'cloud-scoring', ROOT / 'src/bda_pipeline')
    wheels = output / 'work/runtime.gluewheels.zip'
    with zipfile.ZipFile(wheels, 'w') as archive:
        archive.writestr(zipfile.ZipInfo('placeholder.txt', date_time=(2026, 1, 1, 0, 0, 0)), 'synthetic wheels placeholder')
    load_script('scripts/aws/prepare-ingestion.py').prepare(
        packages / 'ingestion', packages / 'cloud-scoring/artifacts', wheels, IMAGE_URI)
    load_script('scripts/aws/prepare-cloud-ingestion.py').prepare(packages / 'cloud-ingestion')
    load_script('scripts/aws/prepare-dashboard-api.py').prepare(packages / 'dashboard-api')
    load_script('scripts/aws/prepare-dashboard-status.py').prepare(packages / 'dashboard-status')
    run_main(load_script('scripts/aws/prepare-sagemaker-container.py'), ['--output', str(packages / 'sagemaker')])
    streaming = load_script('scripts/streaming/prepare-streaming-container.py')
    # The synthetic model cannot match the real release's trusted MANIFEST hash.
    streaming.verify_artifacts = lambda *args: None
    run_main(streaming, ['--artifacts', str(packages / 'cloud-scoring/artifacts'), '--output', str(packages / 'streaming')])
    return {name.name: fingerprint(name) for name in sorted(packages.iterdir())}


def compare(current, reference, output):
    """Diff package members and re-score the reference fixture with the current code."""
    previous = json.loads((reference / 'snapshot.json').read_text())
    report = {'packages': {}}
    for package in sorted(set(current['packages']) | set(previous['packages'])):
        new, old = current['packages'].get(package, {}), previous['packages'].get(package, {})
        changed = sorted(k for k in set(new) & set(old) if new[k] != old[k])
        report['packages'][package] = {
            'added': sorted(set(new) - set(old)),
            'removed': sorted(set(old) - set(new)),
            'changed': [k for k in changed if Path(k).name not in METADATA],
            'metadata_changed': [k for k in changed if Path(k).name in METADATA]}
    # selection.json/comparison.json embed a generation timestamp; compare the
    # trained trees and the feature schema only.
    stable = lambda model: {k: v for k, v in model.items() if k.endswith('.ubj') or k == 'features.json'}
    report['model_identical'] = stable(current['model']) == stable(previous['model'])
    # Re-score the *reference* model and scored rows with the current code.
    rescored = output / 'work/rescored'
    prepare_inference(reference / 'model', reference / 'bq1_scored.parquet', rescored, ROOT / 'src/bda_pipeline')
    for name in ['expected.parquet', 'expected-queue.parquet']:
        new = pd.read_parquet(rescored / 'fixture' / name)
        old = pd.read_parquet(reference / 'packages/cloud-scoring/fixture' / name)
        report[name] = 'identical' if new.equals(old) else 'DIFFERENT'
    (output / 'comparison.json').write_text(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reference', type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a fresh output directory')
    work = args.output / 'work'
    work.mkdir(parents=True)
    scored, dashboard = build_fixture(work)
    compare_models(scored, dashboard, args.output / 'model', rounds=3, threads=1)
    (args.output / 'bq1_scored.parquet').write_bytes(scored.read_bytes())
    snapshot = {'model': fingerprint(args.output / 'model'),
                'packages': build_packages(args.output, args.output / 'model', scored)}
    (args.output / 'snapshot.json').write_text(json.dumps(snapshot, indent=2))
    summary = {name: len(files) for name, files in snapshot['packages'].items()}
    print(json.dumps({'snapshot': str(args.output / 'snapshot.json'), 'files': summary}, indent=2))
    if args.reference:
        report = compare(snapshot, args.reference, args.output)
        print(json.dumps({package: {k: len(v) for k, v in diff.items()} for package, diff in report['packages'].items()}
                         | {k: report[k] for k in ['model_identical', 'expected.parquet', 'expected-queue.parquet']}, indent=2))


if __name__ == '__main__':
    main()
