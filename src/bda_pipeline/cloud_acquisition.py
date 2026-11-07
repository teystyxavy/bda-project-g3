"""Bounded Lambda acquisition; all data and API calls happen in AWS.

What this file is
-----------------
The handler of the ``is459-g3-acquire`` AWS Lambda (container image built from
``cloud-ingestion/Dockerfile``; ``CMD bda_pipeline.cloud_acquisition.handler``).
It is the *first* task of the ``is459-g3-cloud-ingest`` Step Functions state
machine, which is started by ``scripts/ingestion/ingest.py run`` (the default,
non-``--local-acquisition`` path). After this Lambda succeeds, that state
machine starts the main batch state machine ``is459-g3-ingest``.

It does in AWS what a laptop would otherwise do:

1. download HI-Small or HI-Medium transactions + accounts from Kaggle (or reuse a verified
   copy cached in S3),
2. fetch historical FX rates and prepare ``transactions.csv``, ``accounts.csv``
   and ``fx.csv`` (``acquisition.prepare_inputs``),
3. build and validate the run manifest,
4. upload everything to ``s3://<BUCKET>/pipeline/runs/<run_id>/raw/``, writing
   ``manifest.json`` last.

S3 keys used (bucket ``BUCKET``):

* reads  ``pipeline/deployment.json``, ``pipeline/cache/<processing_sha256>.json``,
  ``pipeline/acquisition/latest.json`` and ``pipeline/acquisition/versions/<v>/...``
* writes ``pipeline/runs/<run_id>/raw/{upload-claim.json, transactions.csv,
  accounts.csv, fx.csv, manifest.json}``, the Kaggle file cache under
  ``pipeline/acquisition/``, and on failure
  ``pipeline/runs/<run_id>/evidence/acquisition-failure.json``.

Limits and safety: the Lambda has a 15-minute timeout, 3 GB memory, 10 GB
``/tmp`` and reserved concurrency 1 (see ``infra/cloud-ingestion/main.tf``).
Kaggle credentials come from Secrets Manager only for the duration of the
download and are never logged.
"""
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
from .dataset_options import resolve_subset, selected_files
from .ingest_contract import BUCKET, REGION, CONFIG_KEY, canonical, sha, valid_run, validate_manifest, paths

# S3 transfer settings: 16 MB multipart chunks with only 2 parallel threads,
# to keep memory use predictable inside the Lambda while uploading ~0.5 GB CSVs.
TRANSFER = TransferConfig(max_concurrency=2, multipart_chunksize=16 * 1024 * 1024)
def validate_selection(subset, rows, train):
    """Bound Medium cloud acquisition to the reviewed smoke size."""
    subset = resolve_subset(subset)
    if subset not in ('HI-Small', 'HI-Medium'):
        raise ValueError('Cloud acquisition supports HI-Small and HI-Medium only')
    if subset == 'HI-Medium':
        if train:
            raise ValueError('HI-Medium training is not supported')
        if type(rows) is not int or not 0 < rows <= 1000:
            raise ValueError('HI-Medium cloud acquisition requires --rows between 1 and 1000')
    return subset


def read_json(s3, key):
    """Read a small JSON control document from ``BUCKET``, or ``None`` if absent.

    The Lambda's IAM role only has ``s3:ListBucket`` for a few prefixes, so
    existence is checked with a 1-key prefix listing first (without ListBucket
    permission S3 would return AccessDenied instead of NoSuchKey for a missing
    key). Reads are capped at 100 KB to refuse unexpectedly large documents.

    Args:
        s3: boto3 S3 client.
        key: Exact object key.

    Returns:
        Parsed JSON, or ``None`` when the key does not exist.

    Raises:
        ValueError: if the object is larger than 100,000 bytes.
    """
    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
    # The prefix may match a longer key, so compare the exact key.
    if not any(item['Key'] == key for item in listing.get('Contents', [])): return None
    response = s3.get_object(Bucket=BUCKET, Key=key)
    # Read one byte more than the limit to detect oversize documents.
    with response['Body'] as body: data = body.read(100_001)
    if len(data) > 100_000: raise ValueError('Oversized acquisition control document')
    return json.loads(data)


def request(event):
    """Validate the Step Functions input event and fill in defaults.

    Allowed keys: ``subset`` (defaults to HI-Small), ``run_id`` (required), ``rows`` (optional smoke-test row
    limit), ``dataset_version`` (optional pinned Kaggle version) and the three
    boolean modes ``train``, ``refresh`` and ``rerun`` (default ``False``).
    Unknown keys are rejected so typos cannot be silently ignored.

    Args:
        event: The Lambda event dict.

    Returns:
        A normalised options dict including the validated subset.

    Raises:
        ValueError: for unknown keys, non-boolean modes, a row limit outside
        (0, 5,100,000], a non-positive version, or ``train`` combined with
        ``rows`` (training needs the full chronological dataset).
    """
    if set(event) - {'run_id', 'rows', 'train', 'refresh', 'rerun', 'dataset_version', 'subset'}:
        raise ValueError('Unsupported cloud acquisition option')
    value = {'run_id': valid_run(event['run_id']), 'rows': event.get('rows'),
             'dataset_version': event.get('dataset_version'),
             **{key: event.get(key, False) for key in ['train', 'refresh', 'rerun']}}
    # ``type(...) is bool`` rejects strings such as "false", which are truthy.
    if any(type(value[key]) is not bool for key in ['train', 'refresh', 'rerun']):
        raise ValueError('Modes must be boolean')
    if value['rows'] is not None and (type(value['rows']) is not int or not 0 < value['rows'] <= 5_100_000):
        raise ValueError('Invalid row bound')
    version = value['dataset_version']
    if version is not None and (type(version) is not int or version < 1):
        raise ValueError('Invalid dataset version')
    if value['train'] and value['rows'] is not None: raise ValueError('Training requires the full subset')
    value['subset'] = validate_selection(event.get('subset', 'HI-Small'), value['rows'], value['train'])
    return value


@contextlib.contextmanager
def kaggle_environment(root, secrets, secret_arn):
    """Temporarily set Kaggle credentials and cache location in ``os.environ``.

    ``kagglehub`` reads its credentials and cache folder from environment
    variables. This context manager:

    * points ``KAGGLEHUB_CACHE`` at ``<root>/kaggle-cache`` (inside the
      per-invocation temp directory, so nothing persists between invocations);
    * clears any existing Kaggle credential variables;
    * if ``secret_arn`` is set, loads the Secrets Manager secret, which must be
      either ``{"api_token": ...}`` or ``{"username": ..., "key": ...}``;
    * on exit (success or error) restores every variable to its previous value.

    Without a secret, kagglehub is called with no Kaggle credentials set.

    Args:
        root: Path of the invocation's temporary working directory.
        secrets: boto3 Secrets Manager client (may be ``None`` if no secret).
        secret_arn: ARN from the ``KAGGLE_SECRET_ARN`` env var, or ``''``.

    Raises:
        ValueError: if the secret has an unexpected shape.
    """
    # Credentials exist only during download; the image and Step Functions input contain none.
    keys = ['KAGGLE_API_TOKEN', 'KAGGLE_USERNAME', 'KAGGLE_KEY', 'KAGGLEHUB_CACHE']
    # Remember the original values so they can be restored exactly afterwards.
    old = {key: os.environ.get(key) for key in keys}
    os.environ['KAGGLEHUB_CACHE'] = str(root / 'kaggle-cache')
    try:
        # Remove the three credential variables (not the cache path) before loading fresh ones.
        for key in keys[:3]: os.environ.pop(key, None)
        if secret_arn:
            secret = json.loads(secrets.get_secret_value(SecretId=secret_arn)['SecretString'])
            # Accept exactly one of the two supported credential formats.
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
    """Acquire, prepare and upload one run's raw inputs (the Lambda's real work).

    Flow:

    1. Validate options and load the deployed config (``pipeline/deployment.json``).
    2. Claim the run: write ``raw/upload-claim.json`` with ``IfNoneMatch='*'``
       (a conditional write that fails if the key already exists), so two
       invocations can never write into the same run ID.
    3. Unless ``refresh``: look for a cached, verified copy of the Kaggle files
       in S3 (``pipeline/acquisition/latest.json`` or the pinned version's
       ``record.json``), download it and re-check every SHA-256. A matching
       ``HI-Small_download.json`` is written so ``acquire_dataset`` treats the
       files as verified local reuse.
    4. Call ``acquire_dataset`` (downloads from Kaggle only what is missing),
       with stdout/stderr suppressed so library output cannot leak credentials.
    5. ``prepare_inputs``: optional row limit, fresh FX rates, ``fx.csv``.
    6. Build the manifest (same fields as the laptop launcher in
       ``scripts/ingestion/ingest.py``) and validate it.
    7. Result cache: unless ``rerun``, if a run with the same
       ``processing_sha256`` was already published, return that result and do
       not upload anything - the batch need not run again.
    8. Make sure at least 2 minutes of Lambda time remain, then upload the
       three prepared files (with their SHA-256 in object metadata, which the
       control Lambda later checks) and, if newly downloaded, cache the Kaggle
       files under ``pipeline/acquisition/versions/<v>/``.
    9. Write ``raw/manifest.json`` last (also with ``IfNoneMatch='*'``).

    Args:
        event: Raw Lambda event (re-validated by ``request``).
        clients: ``{'s3': ..., 'secretsmanager': ...}`` boto3 clients.
        root: Temporary working directory (``Path``).
        context: Lambda context (for remaining time), or ``None`` in tests.
        downloader: Optional fake for ``kagglehub.dataset_download`` (tests).
        getter: Optional fake HTTP getter for FX (tests).
        secret_arn: Secrets Manager ARN for Kaggle credentials, or ``''``.

    Returns:
        ``{'cache_hit': True, 'result': {...}}`` when a published result is
        reused, otherwise ``{'cache_hit': False, 'run_id', 'rows',
        'dataset_version'}``.

    Raises:
        ValueError / TimeoutError / botocore errors on any failed check; the
        ``handler`` turns these into a sanitised failure.
    """
    options = request(event)
    run_id = options['run_id']
    subset = options['subset']
    names = selected_files(subset)
    # Preserve existing Small cache paths; isolate Medium pointers and records.
    cache_prefix = 'pipeline/acquisition' + (f'/{subset}' if subset != 'HI-Small' else '')
    s3 = clients['s3']
    config = read_json(s3, CONFIG_KEY)
    if config is None: raise ValueError('Batch deployment is unavailable')
    raw = paths(run_id)['raw']
    # Atomic "claim" of this run ID: fails with PreconditionFailed if it already exists.
    s3.put_object(Bucket=BUCKET, Key=raw + '/upload-claim.json', IfNoneMatch='*',
                  Body=canonical({'run_id': run_id, 'acquisition': 'aws-lambda'}), ServerSideEncryption='AES256')
    directory = root / 'dataset'
    directory.mkdir()
    version = options['dataset_version']
    # A pinned version has its own record; otherwise use the "latest" pointer.
    cache_key = f'{cache_prefix}/versions/{version}/record.json' if version else f'{cache_prefix}/latest.json'
    cached = None if options['refresh'] else read_json(s3, cache_key)
    if cached:
        resolved = cached.get('dataset_version')
        if type(resolved) is not int or resolved < 1 or set(cached.get('files', {})) != set(names):
            raise ValueError('Invalid dataset cache record')
        if version is not None and resolved != version: raise ValueError('Cached dataset version mismatch')
        # Pull the cached Kaggle files from S3 (much faster than Kaggle) and
        # verify each one against the hash recorded when it was cached.
        for name in names:
            s3.download_file(BUCKET, f'{cache_prefix}/versions/{resolved}/{name}', str(directory / name), Config=TRANSFER)
            if digest(directory / name) != cached['files'][name]: raise ValueError('Dataset cache hash mismatch')
        # Seed the local provenance record so acquire_dataset() reuses these files.
        (directory / f'{subset}_download.json').write_text(json.dumps(cached), encoding='utf-8')
    with kaggle_environment(root, clients.get('secretsmanager'), secret_arn):
        # Do not let third-party HTTP errors or progress output expose credential values.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            record = acquire_dataset(directory, subset=subset, version=version, refresh=options['refresh'], downloader=downloader)
    record['origin'] = 'verified-s3-kaggle-reuse' if cached else 'kaggle-api'
    resolved = record['dataset_version']
    # The Kaggle version must be known so the result is reproducible and cacheable.
    if type(resolved) is not int or resolved < 1: raise ValueError('Cloud downloads must record the resolved Kaggle version')
    info = prepare_inputs(directory / names[0], directory / names[1], root / 'prepared', rows=options['rows'], getter=getter)
    # Identity = everything that determines the output; its hash is the
    # processing_sha256 used for result caching (see ingest_contract).
    identity = {**{key: info[key] for key in ['rows', 'files', 'start_date', 'end_date']},
                'subset': subset, 'dataset_version': resolved,
                'deployment_sha256': sha(config), 'train': options['train'],
                # This cloud-acquisition path never selects a date subset.
                'export_dates': None}
    manifest = {**identity, 'schema_version': 1, 'run_id': run_id,
                'processing_sha256': sha(identity), 'rerun': options['rerun'],
                'source_scope': info['scope'], 'currencies': info['currencies'],
                'dataset_origin': record['origin'], 'acquisition_location': 'aws-lambda',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'model_manifest_sha256': config['model_manifest_sha256']}
    validate_manifest(manifest, sha(config))
    # Result cache: identical inputs were already processed and published.
    if not options['rerun']:
        successful = read_json(s3, f"pipeline/cache/{manifest['processing_sha256']}.json")
        if successful and successful.get('status') == 'published':
            return {'cache_hit': True, 'result': {**successful, 'cache_hit': True}}
    # Refuse to start uploads that could be cut off by the 15-minute Lambda
    # timeout; a half-uploaded run is worse than a clean failure.
    if context and context.get_remaining_time_in_millis() < 120_000:
        raise TimeoutError('Insufficient acquisition time remaining for verified upload')
    # Upload prepared inputs; the sha256 metadata is checked by ingest_control 'begin'.
    for name, metadata in manifest['files'].items():
        s3.upload_file(str(root / 'prepared' / name), BUCKET, f'{raw}/{name}',
            ExtraArgs={'ServerSideEncryption': 'AES256', 'Metadata': {'sha256': metadata['sha256']}}, Config=TRANSFER)
    # Cache only the two verified Kaggle files; historical FX is freshly queried each run.
    if not cached:
        for name in names:
            s3.upload_file(str(directory / name), BUCKET, f'{cache_prefix}/versions/{resolved}/{name}',
                ExtraArgs={'ServerSideEncryption': 'AES256', 'Metadata': {'sha256': record['files'][name]}}, Config=TRANSFER)
        # The record is written after the files so it never points at missing data.
        s3.put_object(Bucket=BUCKET, Key=f'{cache_prefix}/versions/{resolved}/record.json',
                      Body=canonical(record), ServerSideEncryption='AES256', ContentType='application/json')
        s3.put_object(Bucket=BUCKET, Key=f'{cache_prefix}/latest.json',
                      Body=canonical(record), ServerSideEncryption='AES256', ContentType='application/json')
    # Commit manifest last: downstream Begin cannot accept an incomplete upload.
    s3.put_object(Bucket=BUCKET, Key=raw + '/manifest.json', Body=canonical(manifest),
                  IfNoneMatch='*', ServerSideEncryption='AES256', ContentType='application/json')
    return {'cache_hit': False, 'run_id': run_id, 'rows': info['rows'], 'dataset_version': resolved}


def handler(event, context):
    """AWS Lambda entry point for ``is459-g3-acquire``.

    Creates boto3 clients (standard retry mode, at most 3 attempts, short
    timeouts), runs ``acquire`` inside a fresh ``/tmp`` directory that is
    deleted afterwards, and returns its result to Step Functions.

    Failure behaviour: any exception is replaced by a generic
    ``RuntimeError`` so provider error messages, URLs or secrets never reach
    CloudWatch Logs or the Step Functions execution history. A sanitised proof
    (only the exception *type*) is written best-effort to
    ``pipeline/runs/<run_id>/evidence/acquisition-failure.json``. Because the
    run ID is already claimed, a retry must use a fresh run ID.

    Args:
        event: Step Functions input (see ``request``).
        context: Lambda context object.

    Returns:
        The dict returned by ``acquire``.
    """
    session = boto3.Session(region_name=REGION)
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3}, connect_timeout=10,
                    read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    clients = {name: session.client(name, config=config) for name in ['s3', 'secretsmanager']}
    # Validate outside the try block: an invalid event has no safe run_id to
    # write failure evidence under, so it simply raises.
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
        # 'from None' drops the original traceback/message from the logged error.
        raise RuntimeError('AWS acquisition failed; inspect acquisition-failure.json; use a fresh run ID.') from None
