"""Bounded SageMaker job contracts (fixed account, region, bucket, role, digest-pinned image, instance and runtime from the run's size profile, at most 500 rounds, network isolation) plus the offline training entry point.
``prepare_bundle``/``validate_bundle``/``build_request`` run locally; ``train_channels`` runs inside the training container and only produces a candidate model."""
import json
import re
import shutil
from pathlib import Path

import pyarrow.dataset as ds

from .ingest_contract import ACCOUNT, BUCKET, MAX_TRAIN_THREADS, REGION, TRAINING_ROLE, training_job_request
from .model import SavedRanker, compare_models, digest

# The deployment target (account, region, bucket, role) comes from ingest_contract, so a request can only target the project's own resources.
ROLE = TRAINING_ROLE
REPOSITORY = f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/is459-g3-training'


def validate_name(name):
    """Enforces the job name rule ``is459-g3-train-<suffix>`` (lowercase letters, digits, hyphens; at most 63 characters) that IAM policies scope to.
    Raises ValueError if the name does not match."""
    # 15-char prefix + 1 + up to 46 + 1 = 63 characters maximum.
    if not re.fullmatch(r'is459-g3-train-[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?', name):
        raise ValueError('Use a safe is459-g3-train- job name of at most 63 characters')


def validate_inputs(scored, snapshot):
    """Proves the training input is the hash-verified full-population baseline export and returns ``{'rows', 'scored_sha256', 'snapshot_sha256'}``.
    Raises ValueError if the snapshot is not full-scope, the hash differs or the row count is zero or differs."""
    snapshot_data = json.loads(Path(snapshot).read_text(encoding='utf-8'))
    # Find the hash the snapshot recorded for bq1_scored.parquet (None if absent).
    expected = next((item['sha256'] for item in snapshot_data['sources']
                     if item['file'] == 'bq1_scored.parquet'), None)
    if snapshot_data.get('aml_scope') != 'full' or digest(scored) != expected:
        raise ValueError('Requires a hash-verified full-population baseline export')
    # count_rows reads Parquet metadata only, so it is cheap even for large files.
    rows = ds.dataset(scored, format='parquet').count_rows()
    if rows <= 0 or rows != snapshot_data['full_rows']:
        raise ValueError('Input row count mismatch')
    return {'rows': rows, 'scored_sha256': expected, 'snapshot_sha256': digest(snapshot)}


def build_request(job_name, image_uri, rows, rounds=300, max_runtime=None):
    """Builds the exact SageMaker ``CreateTrainingJob`` request for the run's size profile (see ``ingest_contract.training_job_request``) after checking the job name and digest-pinned image.
    Raises ValueError if any argument is outside the bounded contract."""
    validate_name(job_name)
    if not re.fullmatch(re.escape(REPOSITORY) + r'@sha256:[0-9a-f]{64}', image_uri):
        raise ValueError('Pin a SHA256 digest in the Group 3 training ECR repository')
    return training_job_request(job_name, image_uri, rows, rounds, max_runtime)


def prepare_bundle(scored, snapshot, output, job_name, image_uri, rounds=300, max_runtime=None):
    """Writes the local bundle (``baseline/``, ``manifest/`` with ``input.json`` provenance, ``request.json``) for one job into a fresh ``output`` directory and returns the request.
    Nothing is uploaded or started here."""
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh preparation directory')
    inputs = validate_inputs(scored, snapshot)
    request = build_request(job_name, image_uri, inputs['rows'], rounds, max_runtime)
    (output / 'baseline').mkdir(parents=True)
    (output / 'manifest').mkdir()
    shutil.copyfile(scored, output / 'baseline/bq1_scored.parquet')
    shutil.copyfile(snapshot, output / 'manifest/dashboard.json')
    # model_promotion=False records that this job can only produce a candidate.
    provenance = {**inputs, 'job_name': job_name, 'image_uri': image_uri,
                  'status': 'prepared-not-submitted', 'model_promotion': False}
    (output / 'manifest/input.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    (output / 'request.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
    return request


def validate_bundle(directory):
    """Re-checks a prepared bundle: inputs must still validate and ``request.json`` must equal a freshly built request, so hand edits are rejected.
    Returns the validated request; raises ValueError on any mismatch."""
    directory = Path(directory)
    provenance = json.loads((directory / 'manifest/input.json').read_text(encoding='utf-8'))
    request = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
    inputs = validate_inputs(directory / 'baseline/bq1_scored.parquet',
                             directory / 'manifest/dashboard.json')
    if any(provenance.get(key) != value for key, value in inputs.items()):
        raise ValueError('Prepared input manifest mismatch')
    expected = build_request(provenance['job_name'], provenance['image_uri'], inputs['rows'],
                             int(request['HyperParameters']['rounds']),
                             request['StoppingCondition']['MaxRuntimeInSeconds'])
    if request != expected:
        raise ValueError('Training request differs from the bounded contract')
    return request


def train_channels(baseline, manifest, work, model_dir, report_dir, rounds=300, threads=4,
                   expected_rows=None):
    """Runs inside the SageMaker container: re-verifies the staged inputs and bounds, runs ``compare_models``, copies the artifacts to ``model_dir`` and reloads them with ``SavedRanker``.
    Raises ValueError on any verification or bounds failure (``train.py`` reports it as the job's FailureReason)."""
    baseline, manifest = Path(baseline), Path(manifest)
    scored, snapshot = baseline / 'bq1_scored.parquet', manifest / 'dashboard.json'
    inputs = validate_inputs(scored, snapshot)
    provenance = json.loads((manifest / 'input.json').read_text(encoding='utf-8'))
    # The data SageMaker staged must be byte-identical to what was reviewed.
    if any(provenance.get(key) != value for key, value in inputs.items()):
        raise ValueError('Staged source differs from reviewed manifest')
    if expected_rows is not None and inputs['rows'] != expected_rows:
        raise ValueError('Job expected row count mismatch')
    if not 1 <= rounds <= 500 or not 1 <= threads <= MAX_TRAIN_THREADS:
        raise ValueError('Training exceeds the bounded contract')
    report = compare_models(scored, snapshot, work, rounds=rounds, threads=threads)
    report['execution'] = {'service': 'sagemaker-training-container',
                           'job_name': provenance['job_name'], 'image_uri': provenance['image_uri'],
                           **inputs, 'promotion': 'candidate-only'}
    # compare_models says "no AWS training", which is no longer true here; replace it with the candidate-only note.
    report['limitations'] = [item for item in report['limitations']
                            if item != 'No model promotion, AWS training, or dashboard replacement.']
    report['limitations'].append('Candidate only; no model promotion or dashboard replacement.')
    # Overwrite comparison.json; selection.json and features.json are untouched, so their hashes stay valid.
    (Path(work) / 'comparison.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    # SageMaker creates /opt/ml/model before entry; compare_models requires a fresh work directory.
    model_dir, report_dir = Path(model_dir), Path(report_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    if any(model_dir.iterdir()):
        raise ValueError('Refuse to overwrite an existing model artifact')
    for path in Path(work).iterdir():
        if path.is_file():
            shutil.copyfile(path, model_dir / path.name)
    # Smoke test: raises if the copied artifacts fail their hash checks.
    SavedRanker(model_dir, threads=threads)
    shutil.copyfile(model_dir / 'comparison.json', report_dir / 'comparison.json')
    print(json.dumps({'status': 'candidate-trained', 'rows': report['total_rows'],
                      'validation_gate_passed': report['selection']['validation_gate_passed'],
                      'model_promoted': False}), flush=True)
    return report
