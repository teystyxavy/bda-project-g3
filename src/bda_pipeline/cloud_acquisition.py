"""Bounded Lambda acquisition; all data and API calls happen in AWS."""
import contextlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

from .acquisition import acquire_dataset, prepare_inputs, digest
from .ingest_contract import BUCKET, REGION, CONFIG_KEY, canonical, sha, valid_run, validate_manifest, paths

TRANSFER = TransferConfig(max_concurrency=2, multipart_chunksize=16 * 1024 * 1024)
NAMES = ['HI-Small_Trans.csv', 'HI-Small_accounts.csv']


def read_json(s3, key):
    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
    if not any(item['Key'] == key for item in listing.get('Contents', [])): return None
    response = s3.get_object(Bucket=BUCKET, Key=key)
    with response['Body'] as body: data = body.read(100_001)
    if len(data) > 100_000: raise ValueError('Oversized acquisition control document')
    return json.loads(data)


def request(event):
    if set(event) - {'run_id', 'rows', 'train', 'refresh', 'rerun', 'dataset_version'}:
        raise ValueError('Unsupported cloud acquisition option')
    value = {'run_id': valid_run(event['run_id']), 'rows': event.get('rows'),
             'dataset_version': event.get('dataset_version'),
             **{key: event.get(key, False) for key in ['train', 'refresh', 'rerun']}}
    if any(type(value[key]) is not bool for key in ['train', 'refresh', 'rerun']):
        raise ValueError('Modes must be boolean')
    if value['rows'] is not None and (type(value['rows']) is not int or not 0 < value['rows'] <= 5_100_000):
        raise ValueError('Invalid row bound')
    version = value['dataset_version']
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError('Invalid dataset version')
    if value['train'] and value['rows'] is not None: raise ValueError('Training requires the full subset')
    return value


@contextlib.contextmanager
def kaggle_environment(root, secrets, secret_arn):
    # Credentials exist only during download; the image and Step Functions input contain none.
    keys = ['KAGGLE_API_TOKEN', 'KAGGLE_USERNAME', 'KAGGLE_KEY', 'KAGGLEHUB_CACHE']
    old = {key: os.environ.get(key) for key in keys}
    os.environ['KAGGLEHUB_CACHE'] = str(root / 'kaggle-cache')
    try:
        for key in keys[:3]: os.environ.pop(key, None)
        if secret_arn:
            secret = json.loads(secrets.get_secret_value(SecretId=secret_arn)['SecretString'])
            if set(secret) == {'api_token'} and isinstance(secret['api_token'], str) and secret['api_token']:
                os.environ['KAGGLE_API_TOKEN'] = secret['api_token']
            elif set(secret) == {'username', 'key'} and all(isinstance(v, str) and v for v in secret.values()):
                os.environ['KAGGLE_USERNAME'], os.environ['KAGGLE_KEY'] = secret['username'], secret['key']
            else: raise ValueError('Invalid Kaggle secret schema')
        yield
    finally:
        for key, value in old.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value


def acquire(event, clients, root, context=None, downloader=None, getter=None, secret_arn=''):
    options = request(event)
    run_id = options['run_id']
    s3 = clients['s3']
    config = read_json(s3, CONFIG_KEY)
    if config is None: raise ValueError('Batch deployment is unavailable')
    raw = paths(run_id)['raw']
    s3.put_object(Bucket=BUCKET, Key=raw + '/upload-claim.json', IfNoneMatch='*',
                  Body=canonical({'run_id': run_id, 'acquisition': 'aws-lambda'}), ServerSideEncryption='AES256')
    directory = root / 'dataset'
    directory.mkdir()
    version = options['dataset_version']
    cache_key = f'pipeline/acquisition/versions/{version}/record.json' if version else 'pipeline/acquisition/latest.json'
    cached = None if options['refresh'] else read_json(s3, cache_key)
    if cached:
        resolved = cached.get('dataset_version')
        if type(resolved) is not int or resolved < 1 or set(cached.get('files', {})) != set(NAMES):
            raise ValueError('Invalid dataset cache record')
        if version is not None and resolved != version: raise ValueError('Cached dataset version mismatch')
        for name in NAMES:
            s3.download_file(BUCKET, f'pipeline/acquisition/versions/{resolved}/{name}', str(directory / name), Config=TRANSFER)
            if digest(directory / name) != cached['files'][name]: raise ValueError('Dataset cache hash mismatch')
        (directory / 'HI-Small_download.json').write_text(json.dumps(cached), encoding='utf-8')
    with kaggle_environment(root, clients.get('secretsmanager'), secret_arn):
        # Do not let third-party HTTP errors or progress output expose credential values.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            record = acquire_dataset(directory, version=version, refresh=options['refresh'], downloader=downloader)
    record['origin'] = 'verified-s3-kaggle-reuse' if cached else 'kaggle-api'
    resolved = record['dataset_version']
    if type(resolved) is not int or resolved < 1: raise ValueError('Cloud downloads must record the resolved Kaggle version')
    info = prepare_inputs(directory / NAMES[0], directory / NAMES[1], root / 'prepared', rows=options['rows'], getter=getter)
    identity = {**{key: info[key] for key in ['rows', 'files', 'start_date', 'end_date']},
                'subset': 'HI-Small', 'dataset_version': resolved,
                'deployment_sha256': sha(config), 'train': options['train']}
    manifest = {**identity, 'schema_version': 1, 'run_id': run_id,
                'processing_sha256': sha(identity), 'rerun': options['rerun'],
                'source_scope': info['scope'], 'currencies': info['currencies'],
                'dataset_origin': record['origin'], 'acquisition_location': 'aws-lambda',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'model_manifest_sha256': config['model_manifest_sha256']}
    validate_manifest(manifest, sha(config))
    if not options['rerun']:
        successful = read_json(s3, f"pipeline/cache/{manifest['processing_sha256']}.json")
        if successful and successful.get('status') == 'published':
            return {'cache_hit': True, 'result': {**successful, 'cache_hit': True}}
    if context and context.get_remaining_time_in_millis() < 120_000:
        raise TimeoutError('Insufficient acquisition time remaining for verified upload')
    for name, metadata in manifest['files'].items():
        s3.upload_file(str(root / 'prepared' / name), BUCKET, f'{raw}/{name}',
            ExtraArgs={'ServerSideEncryption': 'AES256', 'Metadata': {'sha256': metadata['sha256']}}, Config=TRANSFER)
    # Cache only the two verified Kaggle files; historical FX is freshly queried each run.
    if not cached:
        for name in NAMES:
            s3.upload_file(str(directory / name), BUCKET, f'pipeline/acquisition/versions/{resolved}/{name}',
                ExtraArgs={'ServerSideEncryption': 'AES256', 'Metadata': {'sha256': record['files'][name]}}, Config=TRANSFER)
        s3.put_object(Bucket=BUCKET, Key=f'pipeline/acquisition/versions/{resolved}/record.json',
                      Body=canonical(record), ServerSideEncryption='AES256', ContentType='application/json')
        s3.put_object(Bucket=BUCKET, Key='pipeline/acquisition/latest.json',
                      Body=canonical(record), ServerSideEncryption='AES256', ContentType='application/json')
    # Commit manifest last: downstream Begin cannot accept an incomplete upload.
    s3.put_object(Bucket=BUCKET, Key=raw + '/manifest.json', Body=canonical(manifest),
                  IfNoneMatch='*', ServerSideEncryption='AES256', ContentType='application/json')
    return {'cache_hit': False, 'run_id': run_id, 'rows': info['rows'], 'dataset_version': resolved}


def handler(event, context):
    session = boto3.Session(region_name=REGION)
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3}, connect_timeout=10,
                    read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    clients = {name: session.client(name, config=config) for name in ['s3', 'secretsmanager']}
    options = request(event)
    try:
        with tempfile.TemporaryDirectory(prefix='is459-acquire-', dir='/tmp') as folder:
            return acquire(options, clients, Path(folder), context,
                           secret_arn=os.environ.get('KAGGLE_SECRET_ARN', ''))
    except Exception as error:
        # No provider exception messages, request URLs or secret values enter logs/state history.
        proof = {'status': 'failed', 'run_id': options['run_id'], 'stage': 'aws-acquisition',
                 'error_type': type(error).__name__, 'batch_started': False}
        try:
            clients['s3'].put_object(Bucket=BUCKET, Key=f"pipeline/runs/{options['run_id']}/evidence/acquisition-failure.json",
                                    Body=canonical(proof), ServerSideEncryption='AES256', ContentType='application/json')
        except Exception: pass  # Preserve sanitized primary error even if evidence upload fails.
        raise RuntimeError('AWS acquisition failed; inspect acquisition-failure.json; use a fresh run ID.') from None
