"""Standard-library-only contracts shared by the client, Lambda and AWS worker."""
import hashlib
import json
import re
from datetime import date

ACCOUNT = '588738581042'
REGION = 'ap-southeast-1'
BUCKET = f'is459-g3-{ACCOUNT}-{REGION}'
SITE_BUCKET = f'is459-g3-dashboard-{ACCOUNT}-{REGION}'
MACHINE = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:is459-g3-ingest'
DATABASE = 'is459_g3_ingest'
CONFIG_KEY = 'pipeline/deployment.json'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def valid_run(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,35}', run_id):
        raise ValueError('Use a safe run ID of 3-36 lowercase letters, digits or hyphens')
    return run_id


def validate_dates(start, end):
    for value in [start, end]:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Require ISO calendar dates')
        date.fromisoformat(value)
    if not ('2022-09-01' <= start <= end <= '2022-09-18'):
        raise ValueError('Date range outside the reviewed model contract')


def validate_manifest(value, deployment_hash):
    valid_run(value['run_id'])
    if value.get('schema_version') != 1 or value.get('subset') != 'HI-Small':
        raise ValueError('Only the reviewed HI-Small schema/model is currently supported')
    if (type(value.get('rows')) is not int or not 0 < value['rows'] <= 5_100_000
            or type(value.get('train')) is not bool or type(value.get('rerun')) is not bool):
        raise ValueError('Invalid run bounds or mode')
    if value.get('deployment_sha256') != deployment_hash:
        raise ValueError('Deployment changed; prepare again')
    if not re.fullmatch(r'[0-9a-f]{64}', value.get('processing_sha256', '')):
        raise ValueError('Invalid processing identity')
    if set(value.get('files', {})) != {'transactions.csv', 'accounts.csv', 'fx.csv'}:
        raise ValueError('Invalid input allowlist')
    for info in value['files'].values():
        if (not re.fullmatch(r'[0-9a-f]{64}', info.get('sha256', ''))
                or type(info.get('bytes')) is not int or not 0 < info['bytes'] < 2_000_000_000):
            raise ValueError('Invalid source size/hash')
    validate_dates(value['start_date'], value['end_date'])
    identity = {key: value[key] for key in ['subset', 'rows', 'files', 'start_date', 'end_date',
                                           'dataset_version', 'deployment_sha256', 'train']}
    if sha(identity) != value['processing_sha256']:
        raise ValueError('Processing identity mismatch')
    return value


def paths(run_id):
    valid_run(run_id)
    prefix = f'pipeline/runs/{run_id}'
    return {name: f'{prefix}/{name}' for name in ['raw', 'curated', 'scored', 'baseline',
                                                'evidence', 'export', 'release']}


def corridor_query(run_id, start, end):
    valid_run(run_id)
    validate_dates(start, end)
    table = 'run_' + run_id.replace('-', '_')
    return f"""WITH directed AS (
 SELECT 'bank' AS corridor_type, txn_date, from_bank AS from_node, to_bank AS to_node,
 count(*) AS transactions, sum(amount_usd) AS volume_usd FROM {DATABASE}.{table}
 WHERE txn_date BETWEEN '{start}' AND '{end}' GROUP BY txn_date, from_bank, to_bank
 UNION ALL
 SELECT 'currency', txn_date, payment_currency, receiving_currency, count(*), sum(amount_usd)
 FROM {DATABASE}.{table} WHERE txn_date BETWEEN '{start}' AND '{end}'
 GROUP BY txn_date, payment_currency, receiving_currency
), shares AS (SELECT *, volume_usd / nullif(sum(volume_usd) OVER
 (PARTITION BY corridor_type, txn_date), 0) AS volume_share FROM directed)
SELECT *, sum(volume_share*volume_share) OVER (PARTITION BY corridor_type, txn_date)
 AS daily_hhi FROM shares ORDER BY corridor_type, txn_date, volume_usd DESC, from_node, to_node"""


def processing_request(run_id, stage, config, extra_inputs=None):
    p = paths(run_id)
    if stage not in ['baseline', 'export']:
        raise ValueError('Unsupported processing stage')
    sources = {'run': f"{p['raw']}/manifest.json", 'model': config['model_prefix'] + '/'}
    if stage == 'baseline':
        sources.update({'curated': p['curated'] + '/', 'raw': p['raw'] + '/'})
    if stage == 'export':
        sources.update({'baseline': p['baseline'] + '/baseline/bq1_scored.parquet',
                        'baseline_manifest': p['baseline'] + '/manifest/', 'scored': p['scored'] + '/',
                        **(extra_inputs or {})})
    local_paths = {'baseline': 'baseline/baseline', 'baseline_manifest': 'baseline/manifest'}
    return {
        'ProcessingJobName': f'is459-g3-{stage}-{run_id}',
        'RoleArn': config['processing_role_arn'],
        'AppSpecification': {'ImageUri': config['image_uri'],
                             'ContainerEntrypoint': ['python', '/app/pipeline_worker.py'],
                             'ContainerArguments': [stage]},
        'ProcessingInputs': [{'InputName': name, 'S3Input': {
            'S3Uri': f's3://{BUCKET}/{key}', 'LocalPath': '/opt/ml/processing/input/' + local_paths.get(name, name),
            'S3DataType': 'S3Prefix', 'S3InputMode': 'File', 'S3DataDistributionType': 'FullyReplicated'}}
            for name, key in sources.items()],
        'ProcessingOutputConfig': {'Outputs': [{'OutputName': stage,
            'S3Output': {'S3Uri': f"s3://{BUCKET}/{p[stage]}/", 'LocalPath': '/opt/ml/processing/output',
                         'S3UploadMode': 'EndOfJob'}}]},
        'ProcessingResources': {'ClusterConfig': {'InstanceCount': 1,
            'InstanceType': 'ml.m5.2xlarge', 'VolumeSizeInGB': 30}},
        'StoppingCondition': {'MaxRuntimeInSeconds': 1800},
        'NetworkConfig': {'EnableNetworkIsolation': True, 'EnableInterContainerTrafficEncryption': True},
        'Tags': [{'Key': 'Project', 'Value': 'is459-g3'}],
    }
