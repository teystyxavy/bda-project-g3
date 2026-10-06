"""Small AWS control plane. No dataframe libraries; no paid-compute retry loop."""
import hashlib
import json
import os
from urllib.parse import urlsplit

from .ingest_contract import (
    ACCOUNT, REGION, BUCKET, SITE_BUCKET, MACHINE, DATABASE, canonical, sha,
    valid_run, validate_manifest, paths, processing_request, corridor_query,
)
from .release import ASSETS, _inspect, SENSITIVE_TEXT
from .stage_cache import register, reuse


def get_json(s3, key, bucket=BUCKET):
    # Prefix-scoped ListBucket permits reliable absence checks without broad listing.
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
    if not any(item['Key'] == key for item in listing.get('Contents', [])): return None, None
    try:
        item = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return None, None
    with item['Body'] as stream:
        data = stream.read(100_001)
    if len(data) > 100_000: raise ValueError('Control document too large')
    return json.loads(data), item['ETag']


def put_json(s3, key, data, **kwargs):
    return s3.put_object(Bucket=BUCKET, Key=key, Body=canonical(data),
                          ContentType='application/json', ServerSideEncryption='AES256', **kwargs)


def assert_marker(s3, prefix):
    marker = s3.head_object(Bucket=BUCKET, Key=prefix + '/_SUCCESS')
    if marker['ContentLength'] != 0 or marker.get('ServerSideEncryption') != 'AES256':
        raise ValueError('Require the encrypted real success marker')


def owned_lock(s3, run_id):
    lock, etag = get_json(s3, 'pipeline/lock.json')
    if not lock or lock['run_id'] != run_id: raise ValueError('Run does not own the pipeline lock')
    return lock, etag


def release_lock(s3, run_id):
    _, etag = owned_lock(s3, run_id)
    s3.delete_object(Bucket=BUCKET, Key='pipeline/lock.json', IfMatch=etag)


def no_active_workers(clients, run_id):
    p = paths(run_id)
    for job in ['is459-g3-ingest-curate', 'is459-g3-ingest-score']:
        for page in clients['glue'].get_paginator('get_job_runs').paginate(JobName=job):
            for run in page['JobRuns']:
                if (run.get('Arguments', {}).get('--output') == f"s3://{BUCKET}/{p['curated']}/"
                        or run.get('Arguments', {}).get('--OUTPUT') == f"s3://{BUCKET}/{p['scored']}/"):
                    if run['JobRunState'] in ['STARTING', 'RUNNING', 'STOPPING', 'WAITING']:
                        raise ValueError('A Glue worker is still active; preserve the lock')
    sm = clients['sagemaker']
    for kind, name in [('processing', f'is459-g3-baseline-{run_id}'),
                       ('processing', f'is459-g3-export-{run_id}'),
                       ('training', f'is459-g3-train-{run_id}')]:
        operation, key = ('list_processing_jobs', 'ProcessingJobSummaries') if kind == 'processing' else ('list_training_jobs', 'TrainingJobSummaries')
        for page in sm.get_paginator(operation).paginate(NameContains=name):
            for job in page[key]:
                if job[f'{kind.capitalize()}JobName'] == name and job[f'{kind.capitalize()}JobStatus'] in ['InProgress', 'Stopping']:
                    raise ValueError('A SageMaker worker is still active; preserve the lock')


def stop_owned_workers(clients, run_id):
    """Request cancellation only for exact run-owned jobs; keep the run lock."""
    stopped = []
    p = paths(run_id)
    glue, sm = clients['glue'], clients['sagemaker']
    for stage, argument in [('curated', '--output'), ('scored', '--OUTPUT')]:
        name = 'is459-g3-ingest-' + ('curate' if stage == 'curated' else 'score')
        ids = []
        for page in glue.get_paginator('get_job_runs').paginate(JobName=name):
            for job in page['JobRuns']:
                if (job.get('Arguments', {}).get(argument) == f's3://{BUCKET}/{p[stage]}/'
                        and job['JobRunState'] in ['STARTING', 'RUNNING', 'WAITING']):
                    ids.append(job['Id'])
        if ids:
            response = glue.batch_stop_job_run(JobName=name, JobRunIds=ids)
            if response.get('Errors'): raise ValueError('Glue cancellation was rejected; retain lock')
            stopped.extend(ids)
    for kind, stage in [('processing', 'baseline'), ('processing', 'export'), ('training', 'train')]:
        name = f'is459-g3-{stage}-{run_id}'
        operation, key = ('list_processing_jobs', 'ProcessingJobSummaries') if kind == 'processing' else ('list_training_jobs', 'TrainingJobSummaries')
        for page in sm.get_paginator(operation).paginate(NameContains=name):
            for job in page[key]:
                if job[f'{kind.capitalize()}JobName'] == name and job[f'{kind.capitalize()}JobStatus'] == 'InProgress':
                    try:
                        if kind == 'processing': sm.stop_processing_job(ProcessingJobName=name)
                        else: sm.stop_training_job(TrainingJobName=name)
                    except sm.exceptions.ValidationException:
                        # Only tolerate the race where the worker is already terminal/stopping.
                        current = sm.describe_processing_job(ProcessingJobName=name) if kind == 'processing' else sm.describe_training_job(TrainingJobName=name)
                        if current[f'{kind.capitalize()}JobStatus'] == 'InProgress': raise
                    stopped.append(name)
    return stopped


def handle(event, config, clients):
    s3, glue, sm, athena = [clients[name] for name in ['s3', 'glue', 'sagemaker', 'athena']]
    if event.get('source') == 'aws.states':
        detail = event['detail']
        prefix = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:'
        if not detail.get('executionArn', '').startswith(prefix) or detail.get('status') not in ['FAILED', 'ABORTED', 'TIMED_OUT']:
            raise ValueError('Unexpected cancellation event')
        event = {'op': 'failed', 'run_id': detail['executionArn'][len(prefix):],
                 'failure': {'Error': detail['status'], 'Cause': 'Terminal execution event'}}
    run_id = valid_run(event['run_id'])
    p = paths(run_id)
    op = event['op']
    if op == 'recover':
        arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:{run_id}'
        state = clients['stepfunctions'].describe_execution(executionArn=arn)['status']
        if state not in ['FAILED', 'ABORTED', 'TIMED_OUT']:
            raise ValueError('Only a terminal unsuccessful execution can be recovered')
        owned_lock(s3, run_id)
        no_active_workers(clients, run_id)
        release_lock(s3, run_id)
        return {'status': 'lock-released', 'run_id': run_id, 'outputs_preserved': True}
    if op == 'failed':
        failure, _ = get_json(s3, f"{p['evidence']}/failure.json")
        if failure is None:
            put_json(s3, f"{p['evidence']}/failure.json", {'run_id': run_id, 'failure': event.get('failure')})
        lock, _ = get_json(s3, 'pipeline/lock.json')
        if lock and lock.get('run_id') == run_id:
            stopped = stop_owned_workers(clients, run_id)
            put_json(s3, f"{p['evidence']}/cancellation.json", {'run_id': run_id, 'stop_requested': stopped, 'lock_preserved': True})
        return {'status': 'failed-output-preserved', 'run_id': run_id}
    if op == 'processing_failed':
        raise ValueError('Processing failed, stopped or exceeded the bounded completion deadline')
    manifest, _ = get_json(s3, f"{p['raw']}/manifest.json")
    validate_manifest(manifest, sha(config))
    if manifest['run_id'] != run_id or manifest['model_manifest_sha256'] != config['model_manifest_sha256']:
        raise ValueError('Manifest/run/model mismatch')
    if op == 'begin':
        lock, _ = get_json(s3, 'pipeline/lock.json')
        if lock is None:
            put_json(s3, 'pipeline/lock.json', {'run_id': run_id}, IfNoneMatch='*')
        else: owned_lock(s3, run_id)
        for name, info in manifest['files'].items():
            item = s3.head_object(Bucket=BUCKET, Key=f"{p['raw']}/{name}")
            if item['ContentLength'] != info['bytes'] or item.get('Metadata', {}).get('sha256') != info['sha256']:
                raise ValueError('Uploaded raw input contract mismatch')
        for prefix in [p['curated'], p['scored'], p['baseline'], p['export']]:
            if s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix + '/', MaxKeys=1).get('KeyCount', 0):
                raise ValueError('Output already exists; preserve it and use a new run')
        cached = {stage: reuse(s3, stage, manifest, config, get_json) for stage in ['curated', 'scored']}
        put_json(s3, p['evidence'] + '/stage-cache.json', cached)
        return {'train': manifest['train'], 'rows': manifest['rows'],
            'curated_cache_hit': cached['curated']['hit'], 'scored_cache_hit': cached['scored']['hit'],
            'curate_arguments': {'--transactions': f"s3://{BUCKET}/{p['raw']}/transactions.csv",
                '--accounts': f"s3://{BUCKET}/{p['raw']}/accounts.csv", '--fx': f"s3://{BUCKET}/{p['raw']}/fx.csv",
                '--output': f"s3://{BUCKET}/{p['curated']}/", '--expected_rows': str(manifest['rows']),
                '--behaviour': 'true', '--TempDir': f's3://{BUCKET}/tmp/ingest/{run_id}/curate/'},
            'score_arguments': {'--INPUT': f"s3://{BUCKET}/{p['curated']}/",
                '--OUTPUT': f"s3://{BUCKET}/{p['scored']}/", '--EXPECTED_ROWS': str(manifest['rows']),
                '--TempDir': f's3://{BUCKET}/tmp/ingest/{run_id}/score/'},
            'query': corridor_query(run_id, manifest['start_date'], manifest['end_date']),
            'query_token': sha({'run_id': run_id, 'query': 'corridors'})}
    if op == 'publish':
        completed, _ = get_json(s3, f"{p['evidence']}/result.json")
        if completed and completed.get('status') == 'published':
            for stage in ['curated', 'scored']:
                register(s3, stage, manifest, config, get_json, put_json)
            lock, _ = get_json(s3, 'pipeline/lock.json')
            if lock and lock.get('run_id') == run_id: release_lock(s3, run_id)
            return completed
    owned_lock(s3, run_id)
    if op == 'curated':
        assert_marker(s3, p['curated'])
        table = 'run_' + run_id.replace('-', '_')
        location = f"s3://{BUCKET}/{p['curated']}/"
        definition = {'Name': table, 'TableType': 'EXTERNAL_TABLE',
            'Parameters': {'classification': 'parquet', 'projection.enabled': 'true',
                'projection.txn_date.type': 'date', 'projection.txn_date.range': f"{manifest['start_date']},{manifest['end_date']}",
                'projection.txn_date.format': 'yyyy-MM-dd', 'projection.txn_date.interval': '1',
                'projection.txn_date.interval.unit': 'DAYS', 'storage.location.template': location + 'txn_date=${txn_date}/'},
            'PartitionKeys': [{'Name': 'txn_date', 'Type': 'string'}],
            'StorageDescriptor': {'Location': location,
                'Columns': [{'Name': name, 'Type': kind} for name, kind in [
                    ('from_bank', 'string'), ('to_bank', 'string'), ('payment_currency', 'string'),
                    ('receiving_currency', 'string'), ('amount_usd', 'double')]],
                'InputFormat': 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat',
                'OutputFormat': 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat',
                'SerdeInfo': {'SerializationLibrary': 'org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe'}}}
        try:
            glue.create_table(DatabaseName=DATABASE, TableInput=definition)
        except glue.exceptions.AlreadyExistsException:
            existing = glue.get_table(DatabaseName=DATABASE, Name=table)['Table']
            if existing['StorageDescriptor']['Location'] != location:
                raise ValueError('Existing run table has a different source')
        return processing_request(run_id, 'baseline', config)
    if op == 'baseline':
        job = sm.describe_processing_job(ProcessingJobName=f'is459-g3-baseline-{run_id}')
        proof, _ = get_json(s3, f"{p['baseline']}/proof.json")
        if job['ProcessingJobStatus'] != 'Completed' or proof['status'] != 'passed' or proof['rows'] != manifest['rows']:
            raise ValueError('Baseline processing is not verified')
        name = 'is459-g3-train-' + run_id
        training = None
        if manifest['train']:
            for source, target in [('baseline/bq1_scored.parquet', 'baseline/bq1_scored.parquet'),
                                   ('manifest/dashboard.json', 'manifest/dashboard.json'), ('manifest/input.json', 'manifest/input.json')]:
                s3.copy_object(Bucket=BUCKET, Key=f'sagemaker/inputs/{name}/{target}',
                    CopySource={'Bucket': BUCKET, 'Key': f"{p['baseline']}/{source}"}, ServerSideEncryption='AES256')
            training = {'TrainingJobName': name, 'RoleArn': f'arn:aws:iam::{ACCOUNT}:role/is459-g3-sagemaker-training',
                'AlgorithmSpecification': {'TrainingImage': config['image_uri'], 'TrainingInputMode': 'File'},
                'InputDataConfig': [{'ChannelName': channel, 'InputMode': 'File', 'DataSource': {'S3DataSource': {
                    'S3DataType': 'S3Prefix', 'S3Uri': f's3://{BUCKET}/sagemaker/inputs/{name}/{channel}/',
                    'S3DataDistributionType': 'FullyReplicated'}}} for channel in ['baseline', 'manifest']],
                'OutputDataConfig': {'S3OutputPath': f's3://{BUCKET}/sagemaker/outputs'},
                'ResourceConfig': {'InstanceType': 'ml.m5.xlarge', 'InstanceCount': 1, 'VolumeSizeInGB': 20},
                'StoppingCondition': {'MaxRuntimeInSeconds': 1200}, 'HyperParameters': {
                    'rounds': '120', 'threads': '4', 'expected_rows': str(manifest['rows'])},
                'EnableNetworkIsolation': True, 'EnableManagedSpotTraining': False,
                'EnableInterContainerTrafficEncryption': True, 'ProfilerConfig': {'DisableProfiler': True},
                'Tags': [{'Key': 'Project', 'Value': 'is459-g3'}]}
        return {'training_request': training}
    if op == 'export':
        assert_marker(s3, p['scored'])
        baseline, _ = get_json(s3, f"{p['baseline']}/proof.json")
        if not baseline or baseline.get('status') != 'passed' or baseline.get('rows') != manifest['rows']:
            raise ValueError('Export requires independent baseline verification')
        work = event['work']
        if (work['query'] != corridor_query(run_id, manifest['start_date'], manifest['end_date'])
                or work['query_token'] != sha({'run_id': run_id, 'query': 'corridors'})):
            raise ValueError('Unexpected query request')
        query_id = athena.start_query_execution(QueryString=work['query'], WorkGroup='is459-g3-analysis',
            QueryExecutionContext={'Database': DATABASE}, ClientRequestToken=work['query_token'])['QueryExecutionId']
        query = athena.get_query_execution(QueryExecutionId=query_id)['QueryExecution']
        if query['Status']['State'] != 'SUCCEEDED' or query['Query'].strip().removesuffix(';') != work['query'].strip().removesuffix(';'):
            raise ValueError('Query did not complete with the reviewed SQL')
        uri = urlsplit(query['ResultConfiguration']['OutputLocation'])
        if uri.scheme != 's3' or uri.netloc != BUCKET or uri.path.lstrip('/') != f'athena-results/{query_id}.csv':
            raise ValueError('Unexpected Athena output destination')
        extra = {'athena': uri.path.lstrip('/')}
        if manifest['train']:
            trained = sm.describe_training_job(TrainingJobName='is459-g3-train-' + run_id)
            expected = f'sagemaker/outputs/is459-g3-train-{run_id}/output/model.tar.gz'
            if trained['TrainingJobStatus'] != 'Completed' or trained['ModelArtifacts']['S3ModelArtifacts'] != f's3://{BUCKET}/{expected}':
                raise ValueError('Unexpected candidate training output')
            extra['candidate'] = expected
        return processing_request(run_id, 'export', config, extra)
    if op == 'publish':
        job = sm.describe_processing_job(ProcessingJobName=f'is459-g3-export-{run_id}')
        release, _ = get_json(s3, f"{p['export']}/release/manifest.json")
        if (job['ProcessingJobStatus'] != 'Completed' or release['pipeline_run_id'] != run_id
                or release['processing_sha256'] != manifest['processing_sha256']
                or any(release['verification'][kind]['status'] != 'passed' for kind in ['scores', 'corridors'])
                or len(release['files']) != len(ASSETS)
                or {item['file'] for item in release['files']} != set(ASSETS)
                or (manifest['train'] and release['verification'].get('training', {}).get('status') != 'passed')):
            raise ValueError('Unverified or incomplete release')
        blobs = {}
        for item in release['files']:
            if not 0 < item['bytes'] <= 10_000_000: raise ValueError('Oversized release file')
            response = s3.get_object(Bucket=BUCKET, Key=f"{p['export']}/release/site/{item['file']}")
            with response['Body'] as stream: data = stream.read(10_000_001)
            if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
                raise ValueError('Release file hash mismatch')
            if SENSITIVE_TEXT.search(data.decode('utf-8')): raise ValueError('Private content in release')
            blobs[item['file']] = data
        if sum(map(len, blobs.values())) > 12_000_000: raise ValueError('Release too large')
        snapshot = json.loads(blobs['dashboard.json'])
        _inspect(snapshot)
        if snapshot['pipeline']['run_id'] != run_id or snapshot.get('buildStatus') != 'complete':
            raise ValueError('Release snapshot provenance mismatch')
        for name, data in blobs.items():
            s3.put_object(Bucket=SITE_BUCKET, Key=f'dashboard/releases/{run_id}/{name}', Body=data,
                ContentType=ASSETS[name], ServerSideEncryption='AES256', CacheControl='public,max-age=31536000,immutable')
        # Compatible shell assets can load either the previous snapshot or the new pointer.
        for name in ['index.html', 'dashboard.js', 'dashboard.css']:
            s3.put_object(Bucket=SITE_BUCKET, Key=f'dashboard/{name}', Body=blobs[name],
                ContentType=ASSETS[name], ServerSideEncryption='AES256', CacheControl='no-cache')
        pointer, etag = get_json(s3, 'dashboard/current.json', SITE_BUCKET)
        new_pointer = {'schema_version': 1, 'run_id': run_id, 'snapshot': f'releases/{run_id}/dashboard.json',
                       'sha256': hashlib.sha256(blobs['dashboard.json']).hexdigest(), 'previous_run_id': pointer.get('run_id') if pointer else None}
        if not pointer or pointer['run_id'] != run_id:
            s3.put_object(Bucket=SITE_BUCKET, Key='dashboard/current.json', Body=canonical(new_pointer),
                ContentType='application/json', ServerSideEncryption='AES256', CacheControl='no-store,max-age=0',
                **({'IfMatch': etag} if etag else {'IfNoneMatch': '*'}))
        clients['cloudfront'].create_invalidation(DistributionId=config['distribution_id'],
            InvalidationBatch={'Paths': {'Quantity': 4, 'Items': ['/', '/index.html', '/dashboard.js', '/current.json']},
                               'CallerReference': 'ingest-' + run_id})
        result = {'status': 'published', 'run_id': run_id, 'processing_sha256': manifest['processing_sha256'],
                  'dashboard_url': config['dashboard_url'], 'release_url': config['dashboard_url'] + f'releases/{run_id}/index.html',
                  'training_requested': manifest['train'], 'model_promoted': False}
        put_json(s3, f"pipeline/cache/{manifest['processing_sha256']}.json", result)
        put_json(s3, f"{p['evidence']}/result.json", result)
        for stage in ['curated', 'scored']:
            register(s3, stage, manifest, config, get_json, put_json)
        release_lock(s3, run_id)
        return result
    raise ValueError('Unsupported operation')


def lambda_handler(event, context):
    import boto3
    from botocore.config import Config
    config = json.loads(os.environ['PIPELINE_CONFIG'])
    sdk = Config(retries={'mode': 'standard', 'total_max_attempts': 2},
                 connect_timeout=5, read_timeout=20, user_agent_appid='AWSSkill-SageMaker')
    session = boto3.Session(region_name=REGION)
    clients = {name: session.client(name, config=sdk) for name in
               ['s3', 'glue', 'sagemaker', 'athena', 'stepfunctions', 'cloudfront']}
    return handle(event, config, clients)
