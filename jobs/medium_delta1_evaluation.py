"""Run a pinned, mixed-scope frozen-model evaluation and publish an isolated release.

The selected curated partitions retain their original full-history features.
BQ1 is scored afresh; BQ2 reuses verified full-source Glue network tables and
independently recomputes them. No shared deployment, pointer or lock is changed.
"""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import boto3

from bda_pipeline.ingest_contract import BUCKET, REGION, SITE_BUCKET, canonical, paths, sha, validate_manifest
from bda_pipeline.release import ASSETS, ASSET_MAX_BYTES, RELEASE_MAX_BYTES, SENSITIVE_TEXT, _inspect
from bda_pipeline.selected_population import validate_selection, validate_snapshot_population


def localized_module(name):
    from pyspark import SparkFiles
    source = Path(name + '.py')
    if not source.is_file():
        source = Path(SparkFiles.get(name + '.py'))
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def put_immutable(s3, **kwargs):
    """Send a signed conditional PUT even with Glue 5's older S3 API model."""
    event = 'before-sign.s3.PutObject'
    def condition(request, **_):
        request.headers['If-None-Match'] = '*'
    s3.meta.events.register(event, condition)
    try:
        return s3.put_object(**kwargs)
    finally:
        s3.meta.events.unregister(event, condition)


def main():
    from awsglue.utils import getResolvedOptions
    from pyspark.sql import SparkSession
    args = getResolvedOptions(sys.argv, ['RUN_ID', 'CONFIG_KEY', 'DEPLOYMENT_SHA256'])
    s3 = boto3.client('s3', region_name=REGION)
    def read(key):
        response = s3.get_object(Bucket=BUCKET, Key=key)
        with response['Body'] as stream:
            return stream.read()
    config = json.loads(read(args['CONFIG_KEY']))
    if sha(config) != args['DEPLOYMENT_SHA256'] or args['RUN_ID'] != config['run_id']:
        raise ValueError('Pinned evaluation config mismatch')
    run_id = args['RUN_ID']
    p = paths(run_id)
    manifest = json.loads(read(p['raw'] + '/manifest.json'))
    validate_manifest(manifest, sha(config))
    validate_selection(manifest)
    spark = SparkSession.builder.appName('is459-medium-delta1-evaluation').getOrCreate()
    date_stage = localized_module('date_evaluation_stage')
    original_argv = sys.argv[:]
    def stage(name):
        print(json.dumps({'stage': name, 'status': 'starting', 'run_id': run_id}), flush=True)
        sys.argv = [original_argv[0], '--RUN_ID', run_id, '--STAGE', name,
                    '--CONFIG_KEY', args['CONFIG_KEY'], '--DEPLOYMENT_SHA256', args['DEPLOYMENT_SHA256']]
        try:
            date_stage.main()
        finally:
            sys.argv = original_argv[:]
        print(json.dumps({'stage': name, 'status': 'committed'}), flush=True)
    stage('baseline')
    scoring = localized_module('score_transactions')
    print(json.dumps({'stage': 'score', 'status': 'starting', 'rows': manifest['rows']}), flush=True)
    scoring.score(spark, f's3://{BUCKET}/{p["curated"]}/', f's3://{BUCKET}/{p["scored"]}/',
                  '.', manifest['model_manifest_sha256'], manifest['rows'])
    stage('export')
    publish_release(s3, config, manifest)


def publish_release(s3, config, manifest):
    """Validate completed exports and publish without repeating Spark compute."""
    run_id = config['run_id']
    p = paths(run_id)
    validate_manifest(manifest, sha(config))
    validate_selection(manifest)
    def read(key):
        with s3.get_object(Bucket=BUCKET, Key=key)['Body'] as stream:
            return stream.read()
    release = json.loads(read(p['export'] + '/release/manifest.json'))
    if (release['pipeline_run_id'] != run_id or release['processing_sha256'] != manifest['processing_sha256']
            or release['verification']['scores']['rows'] != manifest['rows']
            or release['verification']['network']['reference_rows'] != manifest['network_rows']
            or any(release['verification'][kind]['status'] != 'passed' for kind in ['scores', 'network'])
            or len(release['files']) != len(ASSETS) or {item['file'] for item in release['files']} != set(ASSETS)):
        raise ValueError('Incomplete mixed-scope export')
    blobs = {}
    for item in release['files']:
        name = item['file']
        if not 0 < item['bytes'] <= ASSET_MAX_BYTES[name]:
            raise ValueError('Release file exceeds budget')
        blob = read(p['export'] + '/release/site/' + name)
        if len(blob) != item['bytes'] or hashlib.sha256(blob).hexdigest() != item['sha256'] or SENSITIVE_TEXT.search(blob.decode()):
            raise ValueError('Release asset validation failed')
        blobs[name] = blob
    if sum(map(len, blobs.values())) > RELEASE_MAX_BYTES:
        raise ValueError('Release exceeds package budget')
    snapshot = json.loads(blobs['dashboard.json'])
    _inspect(snapshot)
    validate_snapshot_population(snapshot)
    if (snapshot['model']['provenance']['model_sha256'] != config['model_sha256']
            or snapshot['network']['population']['rows'] != manifest['network_rows']
            or snapshot['population']['rows'] != manifest['rows']):
        raise ValueError('Snapshot model or scope mismatch')
    for name, blob in blobs.items():
        key = f'dashboard/releases/{run_id}/{name}'
        put_immutable(s3, Bucket=SITE_BUCKET, Key=key, Body=blob, ContentType=ASSETS[name],
                      ServerSideEncryption='AES256', CacheControl='public,max-age=31536000,immutable')
        response = s3.get_object(Bucket=SITE_BUCKET, Key=key)
        with response['Body'] as stream:
            if stream.read() != blob:
                raise ValueError('Published object mismatch')
    result = {'status': 'published-isolated-release', 'run_id': run_id, 'model': 'hist-depth5-delta1',
              'model_sha256': config['model_sha256'], 'bq1_rows': manifest['rows'], 'bq2_rows': manifest['network_rows'],
              'bq1_start_date': manifest['start_date'], 'bq1_end_date': manifest['end_date'],
              'release_url': config['dashboard_url'] + f'releases/{run_id}/index.html',
              'main_pointer_unchanged': True, 'training_requested': False}
    s3.put_object(Bucket=BUCKET, Key=p['evidence'] + '/result.json', Body=canonical(result),
                  ContentType='application/json', ServerSideEncryption='AES256')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
