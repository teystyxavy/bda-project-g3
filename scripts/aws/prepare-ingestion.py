"""Prepare immutable ingestion artifacts and Terraform variables; no AWS calls."""
import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from bda_pipeline.ingest_contract import ACCOUNT, REGION, BUCKET, sha
from bda_pipeline.inference import verify_artifacts


def digest(path):
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def zip_sources(target, names, minimal=False):
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            info = zipfile.ZipInfo('bda_pipeline/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            data = b'"""Ingestion control package."""\n' if minimal and name == '__init__.py' else (ROOT / 'src/bda_pipeline' / name).read_bytes()
            archive.writestr(info, data)


def prepare(output, models, wheels, image_uri):
    if output.exists(): raise ValueError('Use a fresh preparation directory')
    if not re.fullmatch(f'{ACCOUNT}\\.dkr\\.ecr\\.{REGION}\\.amazonaws\\.com/is459-g3-training@sha256:[0-9a-f]{{64}}', image_uri):
        raise ValueError('Require the pinned Group 3 ECR image digest')
    model_hash = digest(models / 'model-manifest.json')
    manifest = verify_artifacts(models, model_hash)
    output.mkdir(parents=True)
    # No dataset, state, credentials, or unrelated workspace files in these packages.
    zip_sources(output / 'bda_pipeline.zip', [p.name for p in (ROOT / 'src/bda_pipeline').glob('*.py')])
    zip_sources(output / 'control.zip', ['__init__.py', 'ingest_contract.py', 'ingest_control.py', 'stage_cache.py', 'release.py'], minimal=True)
    for name in ['curate_transactions.py', 'score_transactions.py']:
        shutil.copyfile(ROOT / 'jobs' / name, output / name)
    shutil.copyfile(wheels, output / 'runtime.gluewheels.zip')
    (output / 'model').mkdir()
    files = ['model-manifest.json', 'comparison.json', 'features.json', 'selection.json']
    selection = json.loads((models / 'selection.json').read_text())
    files.append(selection['selected']['model_file'])
    for name in files:
        if Path(name).name != name: raise ValueError('Unsafe model file')
        shutil.copyfile(models / name, output / 'model' / name)
    hashes = {p.relative_to(output).as_posix(): digest(p) for p in output.rglob('*') if p.is_file()}
    prefix = 'pipeline/deployments/' + sha({'files': hashes, 'image_uri': image_uri})
    config = {'schema_version': 1, 'artifact_prefix': prefix, 'model_prefix': prefix + '/model',
        'model_manifest_sha256': model_hash, 'image_uri': image_uri,
        'processing_role_arn': f'arn:aws:iam::{ACCOUNT}:role/is459-g3-ingest-processing',
        'distribution_id': 'EU1WB6XM4GKHQ', 'dashboard_url': 'https://d1n61tlw9pcrog.cloudfront.net/'}
    config['stage_versions'] = {
        'curated': sha({'code': hashes['curate_transactions.py'], 'schema': 1, 'runtime': 'glue-5.0-python3.11'}),
        'scored': sha({'code': hashes['score_transactions.py'], 'package': hashes['bda_pipeline.zip'],
                       'wheels': hashes['runtime.gluewheels.zip'], 'runtime': 'glue-5.0-python3.11'})}
    (output / 'model/deployment.json').write_text(json.dumps(config, indent=2))
    (output / 'deployment.json').write_text(json.dumps(config, indent=2))
    variables = {'artifact_directory': str(output.resolve()).replace('\\', '/'), 'pipeline_config': config,
                 'model_file': files[-1]}
    (output / 'terraform.tfvars.json').write_text(json.dumps(variables, indent=2))
    (output / 'artifact-hashes.json').write_text(json.dumps({p.relative_to(output).as_posix(): digest(p)
        for p in output.rglob('*') if p.is_file() and p.name != 'artifact-hashes.json'}, indent=2))
    print(json.dumps({'status': 'prepared-not-deployed', 'deployment_sha256': sha(config), 'artifact_prefix': prefix}))
    return config


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--wheels', type=Path, required=True)
    parser.add_argument('--image-uri', required=True)
    args = parser.parse_args()
    prepare(args.output, args.models, args.wheels, args.image_uri)
