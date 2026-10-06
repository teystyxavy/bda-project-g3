"""Bounded SageMaker job contracts and a reusable, offline training entry point."""
import json
import re
import shutil
from pathlib import Path

import pyarrow.dataset as ds

from .model import SavedRanker, compare_models, digest

ACCOUNT = '588738581042'
REGION = 'ap-southeast-1'
BUCKET = f'is459-g3-{ACCOUNT}-{REGION}'
ROLE = f'arn:aws:iam::{ACCOUNT}:role/is459-g3-sagemaker-training'
REPOSITORY = f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/is459-g3-training'


def validate_name(name):
    if not re.fullmatch(r'is459-g3-train-[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?', name):
        raise ValueError('Use a safe is459-g3-train- job name of at most 63 characters')


def validate_inputs(scored, snapshot):
    snapshot_data = json.loads(Path(snapshot).read_text(encoding='utf-8'))
    expected = next((item['sha256'] for item in snapshot_data['sources']
                     if item['file'] == 'bq1_scored.parquet'), None)
    if snapshot_data.get('aml_scope') != 'full' or digest(scored) != expected:
        raise ValueError('Requires a hash-verified full-population baseline export')
    rows = ds.dataset(scored, format='parquet').count_rows()
    if rows <= 0 or rows != snapshot_data['full_rows']:
        raise ValueError('Input row count mismatch')
    return {'rows': rows, 'scored_sha256': expected, 'snapshot_sha256': digest(snapshot)}


def build_request(job_name, image_uri, rows, rounds=120, max_runtime=1200):
    validate_name(job_name)
    if not re.fullmatch(re.escape(REPOSITORY) + r'@sha256:[0-9a-f]{64}', image_uri):
        raise ValueError('Pin a SHA256 digest in the Group 3 training ECR repository')
    if not isinstance(rows, int) or isinstance(rows, bool) or rows <= 0:
        raise ValueError('Expected rows must be positive')
    if not 1 <= rounds <= 120 or not 60 <= max_runtime <= 1800:
        raise ValueError('Allow 1-120 rounds and a 60-1800 second runtime')
    prefix = f's3://{BUCKET}/sagemaker/inputs/{job_name}'
    return {
        'TrainingJobName': job_name, 'RoleArn': ROLE,
        'AlgorithmSpecification': {'TrainingImage': image_uri, 'TrainingInputMode': 'File'},
        'InputDataConfig': [
            {'ChannelName': channel, 'DataSource': {'S3DataSource': {
                'S3DataType': 'S3Prefix', 'S3Uri': f'{prefix}/{channel}/',
                'S3DataDistributionType': 'FullyReplicated'}}, 'InputMode': 'File'}
            for channel in ['baseline', 'manifest']],
        'OutputDataConfig': {'S3OutputPath': f's3://{BUCKET}/sagemaker/outputs'},
        'ResourceConfig': {'InstanceType': 'ml.m5.xlarge', 'InstanceCount': 1, 'VolumeSizeInGB': 20},
        'StoppingCondition': {'MaxRuntimeInSeconds': max_runtime},
        'HyperParameters': {'rounds': str(rounds), 'threads': '4', 'expected_rows': str(rows)},
        'EnableNetworkIsolation': True, 'EnableInterContainerTrafficEncryption': True,
        'EnableManagedSpotTraining': False,
        'ProfilerConfig': {'DisableProfiler': True},
        'Tags': [{'Key': 'Project', 'Value': 'is459-g3'},
                 {'Key': 'Component', 'Value': 'SageMakerTraining'}],
    }


def prepare_bundle(scored, snapshot, output, job_name, image_uri, rounds=120, max_runtime=1200):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh preparation directory')
    inputs = validate_inputs(scored, snapshot)
    request = build_request(job_name, image_uri, inputs['rows'], rounds, max_runtime)
    (output / 'baseline').mkdir(parents=True)
    (output / 'manifest').mkdir()
    shutil.copyfile(scored, output / 'baseline/bq1_scored.parquet')
    shutil.copyfile(snapshot, output / 'manifest/dashboard.json')
    provenance = {**inputs, 'job_name': job_name, 'image_uri': image_uri,
                  'status': 'prepared-not-submitted', 'model_promotion': False}
    (output / 'manifest/input.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    (output / 'request.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
    return request


def validate_bundle(directory):
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


def train_channels(baseline, manifest, work, model_dir, report_dir, rounds=120, threads=4,
                   expected_rows=None):
    """SageMaker stages S3 files; training code needs no AWS credentials or network."""
    baseline, manifest = Path(baseline), Path(manifest)
    scored, snapshot = baseline / 'bq1_scored.parquet', manifest / 'dashboard.json'
    inputs = validate_inputs(scored, snapshot)
    provenance = json.loads((manifest / 'input.json').read_text(encoding='utf-8'))
    if any(provenance.get(key) != value for key, value in inputs.items()):
        raise ValueError('Staged source differs from reviewed manifest')
    if expected_rows is not None and inputs['rows'] != expected_rows:
        raise ValueError('Job expected row count mismatch')
    if not 1 <= rounds <= 120 or not 1 <= threads <= 4:
        raise ValueError('Training exceeds the bounded contract')
    report = compare_models(scored, snapshot, work, rounds=rounds, threads=threads)
    report['execution'] = {'service': 'sagemaker-training-container',
                           'job_name': provenance['job_name'], 'image_uri': provenance['image_uri'],
                           **inputs, 'promotion': 'candidate-only'}
    report['limitations'] = [item for item in report['limitations']
                            if item != 'No model promotion, AWS training, or dashboard replacement.']
    report['limitations'].append('Candidate only; no model promotion or dashboard replacement.')
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
    SavedRanker(model_dir, threads=threads)
    shutil.copyfile(model_dir / 'comparison.json', report_dir / 'comparison.json')
    print(json.dumps({'status': 'candidate-trained', 'rows': report['total_rows'],
                      'validation_gate_passed': report['selection']['validation_gate_passed'],
                      'model_promoted': False}), flush=True)
    return report
