"""Launch an isolated HI-Medium evaluation using the reviewed frozen Delta 1 model.

AWS writes and paid Glue execution are intentional. BQ1 excludes training and
validation dates; BQ2 retains the complete verified source. Publication is at
an immutable release URL; no shared deployment, pointer or lock is changed.
"""
import hashlib
import importlib.util
import json
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from bda_pipeline.ingest_contract import ACCOUNT, BUCKET, REGION, SITE_BUCKET, canonical, paths, sha, validate_manifest
from bda_pipeline.inference import verify_artifacts

SOURCE = '20261107-072313-79997f49'
MODEL_HASH = '0170bef63f4cd257cbcda8c4748cea0dcf90f202134105fb8226d6dde6a0646f'
MODEL_MANIFEST_HASH = '0b9ba30d77237c9f71a1799235316d9f6dfba042dc70c1c31546513da0ea4b6a'
TRAINING_KEY = 'sagemaker/inputs/is459-g3-train-datesubset-verify1/manifest/dashboard.json'


def main():
    session = boto3.Session(profile_name='is459-g3-tools', region_name=REGION)
    sdk = Config(connect_timeout=10, read_timeout=120, retries={'mode': 'standard', 'max_attempts': 3})
    s3, glue, iam, states = [session.client(name, config=sdk) for name in ['s3', 'glue', 'iam', 'stepfunctions']]
    if session.client('sts').get_caller_identity()['Account'] != ACCOUNT:
        raise ValueError('Wrong account')
    def read(key):
        with s3.get_object(Bucket=BUCKET, Key=key)['Body'] as stream:
            return stream.read()
    def put(key, data):
        s3.put_object(Bucket=BUCKET, Key=key, Body=data, IfNoneMatch='*', ServerSideEncryption='AES256')
    source = paths(SOURCE)
    source_manifest = json.loads(read(source['raw'] + '/manifest.json'))
    source_proof = json.loads(read(source['baseline'] + '/proof.json'))
    validate_manifest(source_manifest, source_manifest['deployment_sha256'])
    if source_manifest['rows'] != 31_898_238 or source_proof['status'] != 'passed' or source_proof['rows'] != source_manifest['rows']:
        raise ValueError('Unverified Medium source')
    upstream = {}
    arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:{SOURCE}'
    for page in states.get_paginator('get_execution_history').paginate(executionArn=arn):
        for event in page['events']:
            detail = event.get('taskSucceededEventDetails', {})
            if detail.get('resourceType') == 'glue':
                item = json.loads(detail['output'])
                job = glue.get_job_run(JobName=item['JobName'], RunId=item['Id'])['JobRun']
                if job['JobRunState'] != 'SUCCEEDED':
                    raise ValueError('Upstream job no longer successful')
                upstream[item['JobName']] = item['Id']
    if not all('is459-g3-ingest-' + stage in upstream for stage in ['curate', 'score', 'network', 'baseline']):
        raise ValueError('Missing upstream success evidence')
    model = ROOT / 'output/delta1-promotion-review-20261107/model'
    artifacts = verify_artifacts(model, MODEL_MANIFEST_HASH)
    selection = json.loads((model / 'selection.json').read_text())
    report = json.loads((model / 'comparison.json').read_text())
    if selection['selected']['sha256'] != MODEL_HASH or selection['selected']['name'] != 'hist-depth5-delta1':
        raise ValueError('Unexpected model')
    training_bytes = read(TRAINING_KEY)
    training = json.loads(training_bytes)
    training_hash = hashlib.sha256(training_bytes).hexdigest()
    if training_hash != report['execution']['snapshot_sha256']:
        raise ValueError('Training snapshot mismatch')
    days = sorted(day for day in source_proof['daily'] if day >= report['splits']['validation'][1])
    if days[0] != '2022-09-15' or days[-1] != '2022-09-28':
        raise ValueError('Unexpected evaluation dates')
    rows = sum(source_proof['daily'][d]['rows'] for d in days)
    run_id = 'medium-delta1-' + datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d-%H%M%S')
    p = paths(run_id)
    local = ROOT / 'output' / run_id
    local.mkdir()
    spec = importlib.util.spec_from_file_location('prepare_ingestion', ROOT / 'scripts/aws/prepare-ingestion.py')
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    prepare.zip_sources(local / 'bda_pipeline.zip', [f.name for f in (ROOT / 'src/bda_pipeline').glob('*.py')])
    files = {'bda_pipeline.zip': local / 'bda_pipeline.zip'}
    for name in ['medium_delta1_evaluation.py', 'date_evaluation_stage.py', 'score_transactions.py']:
        files[name] = ROOT / 'jobs' / name
    for name in ['index.html', 'dashboard.js', 'dashboard.css']:
        files['dashboard/' + name] = ROOT / 'dashboard' / name
    for name in [*artifacts['files'], 'model-manifest.json']:
        files['model/' + name] = model / name
    hashes = {key: hashlib.sha256(file.read_bytes()).hexdigest() for key, file in files.items()}
    artifact_prefix = 'pipeline/evaluations/' + sha(hashes)
    for key, file in files.items():
        remote = artifact_prefix + '/' + key
        try:
            existing = read(remote)
        except s3.exceptions.NoSuchKey:
            put(remote, file.read_bytes())
        else:
            if hashlib.sha256(existing).hexdigest() != hashes[key]:
                raise ValueError('Immutable artifact changed')
    config = {'schema_version': 1, 'run_id': run_id, 'artifact_prefix': artifact_prefix,
              'model_prefix': artifact_prefix + '/model', 'model_manifest_sha256': MODEL_MANIFEST_HASH,
              'model_sha256': MODEL_HASH, 'dashboard_url': 'https://d1n61tlw9pcrog.cloudfront.net/',
              'source_run_id': SOURCE, 'artifact_hashes': hashes}
    config_key = p['evidence'] + '/evaluation-config.json'
    chosen = {'kind': 'date-range', 'start_date': days[0], 'end_date': days[-1],
              'source_run_id': SOURCE, 'source_manifest_sha256': sha(source_manifest),
              'source_proof_sha256': sha(source_proof), 'training_snapshot_sha256': training_hash,
              'source_curated_prefix': source['curated'] + '/', 'development_dates': training['provenance']['export_dates'],
              'network_scope': 'full-source', 'network_start_date': source_manifest['start_date'],
              'network_end_date': source_manifest['end_date']}
    manifest = {**source_manifest, 'run_id': run_id, 'rows': rows, 'start_date': days[0], 'end_date': days[-1],
                'source_index_rows': source_manifest['rows'], 'network_rows': source_manifest['rows'],
                'source_scope': 'date-subset', 'selection': chosen, 'model_manifest_sha256': MODEL_MANIFEST_HASH,
                'deployment_sha256': sha(config), 'train': False, 'rerun': False, 'export_dates': None,
                'created_at': datetime.now(timezone.utc).isoformat()}
    identity = {k: manifest[k] for k in ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version',
                                       'deployment_sha256', 'train', 'export_dates', 'selection', 'source_index_rows', 'network_rows']}
    manifest['processing_sha256'] = sha(identity)
    validate_manifest(manifest, sha(config))
    objects = []
    for stage in ['curated', 'network']:
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=source[stage] + '/'):
            for item in page.get('Contents', []):
                relative = item['Key'][len(source[stage]) + 1:]
                if stage == 'curated' and not any(relative.startswith('txn_date=' + day + '/') for day in days):
                    continue
                if not relative.endswith('.parquet') and relative != '_SUCCESS':
                    continue
                objects.append({'source_key': item['Key'], 'destination_key': p[stage] + '/' + relative,
                                'bytes': item['Size'], 'source_etag': item['ETag']})
    if not objects:
        raise ValueError('Missing evaluation inputs')
    def copy(item):
        s3.copy_object(Bucket=BUCKET, Key=item['destination_key'],
                       CopySource={'Bucket': BUCKET, 'Key': item['source_key']},
                       CopySourceIfMatch=item['source_etag'], ServerSideEncryption='AES256')
        head = s3.head_object(Bucket=BUCKET, Key=item['destination_key'])
        if head['ContentLength'] != item['bytes'] or head['ServerSideEncryption'] != 'AES256':
            raise ValueError('Copied input mismatch')
        return {**item, 'destination_etag': head['ETag']}
    with ThreadPoolExecutor(max_workers=6) as executor:
        copied = list(executor.map(copy, objects))
    for filename, data in [('manifest.json', canonical(source_manifest)), ('proof.json', canonical(source_proof)),
                           ('training-dashboard.json', training_bytes)]:
        put(p['evidence'] + '/source/' + filename, data)
    put(p['evidence'] + '/selection-inputs.json', canonical({'source_manifest_sha256': sha(source_manifest), 'objects': copied, 'upstream_jobs': upstream}))
    put(p['raw'] + '/manifest.json', canonical(manifest))
    put(config_key, canonical(config))
    job_name = 'is459-g3-' + run_id
    role_name = job_name
    role_arn = f'arn:aws:iam::{ACCOUNT}:role/{role_name}'
    trust = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'glue.amazonaws.com'},
             'Action': 'sts:AssumeRole', 'Condition': {'StringEquals': {'aws:SourceAccount': ACCOUNT},
             'ArnEquals': {'aws:SourceArn': f'arn:aws:glue:{REGION}:{ACCOUNT}:job/{job_name}'}}}]}
    current = json.loads(read('pipeline/deployment.json'))
    wheels = current['artifact_prefix'] + '/runtime.gluewheels.zip'
    read_prefixes = [f'pipeline/runs/{run_id}/*', source['curated'] + '/*', artifact_prefix + '/*', wheels]
    bucket_arn = 'arn:aws:s3:::' + BUCKET
    site_arn = 'arn:aws:s3:::' + SITE_BUCKET
    policy = {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Action': ['s3:GetBucketLocation'], 'Resource': [bucket_arn, site_arn]},
        {'Effect': 'Allow', 'Action': ['s3:ListBucket'], 'Resource': bucket_arn, 'Condition': {'StringLike': {'s3:prefix': read_prefixes}}},
        {'Effect': 'Allow', 'Action': ['s3:GetObject'], 'Resource': [bucket_arn + '/' + value for value in read_prefixes]},
        {'Effect': 'Allow', 'Action': ['s3:PutObject', 's3:AbortMultipartUpload', 's3:ListMultipartUploadParts'],
         'Resource': [bucket_arn + '/' + p[stage] + '/*' for stage in ['baseline', 'scored', 'export', 'evidence']]},
        {'Effect': 'Allow', 'Action': ['s3:DeleteObject'], 'Resource': bucket_arn + '/' + p['scored'] + '/*'},
        {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:PutObject', 's3:DeleteObject'],
         'Resource': bucket_arn + '/' + p['scored'] + '_$folder$'},
        {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:PutObject'], 'Resource': site_arn + '/dashboard/releases/' + run_id + '/*'},
        {'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
         'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws-glue/{job_name}*:*'},
        {'Effect': 'Allow', 'Action': ['cloudwatch:PutMetricData'], 'Resource': '*', 'Condition': {'StringEquals': {'cloudwatch:namespace': 'Glue'}}}
    ]}
    iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust),
                    Description='One pinned Medium Delta1 evaluation; immutable publication only',
                    Tags=[{'Key': 'Project', 'Value': 'is459-g3'}])
    iam.put_role_policy(RoleName=role_name, PolicyName='PinnedEvaluationOnly', PolicyDocument=json.dumps(policy))
    uri = lambda key: 's3://' + BUCKET + '/' + key
    extra = [artifact_prefix + '/dashboard/' + name for name in ['index.html', 'dashboard.js', 'dashboard.css']]
    extra += [artifact_prefix + '/' + name for name in ['date_evaluation_stage.py', 'score_transactions.py']]
    extra += [artifact_prefix + '/model/' + name for name in [*artifacts['files'], 'model-manifest.json']]
    arguments = {'--RUN_ID': run_id, '--CONFIG_KEY': config_key, '--DEPLOYMENT_SHA256': sha(config),
                 '--extra-py-files': uri(artifact_prefix + '/bda_pipeline.zip'),
                 '--extra-files': ','.join(map(uri, extra)), '--additional-python-modules': uri(wheels),
                 '--python-modules-installer-option': '--no-index', '--job-bookmark-option': 'job-bookmark-disable',
                 '--custom-logGroup-prefix': '/aws-glue/' + job_name,
                 '--conf': 'spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs=true'}
    glue.create_job(Name=job_name, Role=role_arn, Command={'Name': 'glueetl', 'ScriptLocation': uri(artifact_prefix + '/medium_delta1_evaluation.py'), 'PythonVersion': '3'},
                    NonOverridableArguments=arguments, GlueVersion='5.0', WorkerType='G.2X', NumberOfWorkers=2,
                    Timeout=90, MaxRetries=0, ExecutionProperty={'MaxConcurrentRuns': 1}, Tags={'Project': 'is459-g3', 'Component': 'MediumDelta1Evaluation'})
    receipt = {'run_id': run_id, 'job_name': job_name, 'status': 'prepared', 'source_run_id': SOURCE,
               'bq1_rows': rows, 'bq1_start_date': days[0], 'bq1_end_date': days[-1],
               'bq2_rows': source_manifest['rows'], 'model': selection['selected']['name'], 'model_sha256': MODEL_HASH,
               'training_requested': False, 'release_url': config['dashboard_url'] + f'releases/{run_id}/index.html',
               'role_arn': role_arn, 'config_key': config_key, 'workers': 2, 'worker_type': 'G.2X', 'timeout_minutes': 90}
    (local / 'submission.json').write_bytes(canonical(receipt))
    (local / 'policy.json').write_bytes(canonical(policy))
    (local / 'manifest.json').write_bytes(canonical(manifest))
    print(json.dumps(receipt), flush=True)
    (ROOT / 'output/medium-delta1-20261107/prepared-run.json').write_bytes(canonical(receipt))
    started = glue.start_job_run(JobName=job_name)
    receipt.update(status='SUBMITTED', job_run_id=started['JobRunId'])
    (local / 'submission.json').write_bytes(canonical(receipt))
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()
