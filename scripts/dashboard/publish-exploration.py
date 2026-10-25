"""Publish a reviewed four-asset release to the existing Group 3 dashboard only.

This command writes to AWS. Run only when publication is explicitly requested.
It verifies the local release manifest, account, pipeline lock and expected
current pointer; saves predecessor assets under --evidence; uploads an immutable
release; updates the dashboard assets/pointer; and invalidates CloudFront.
Reads a prepared --release folder and --expected-pointer JSON. --run-id names
the new release, and --evidence must be a fresh local directory. Publication
involves multiple writes, so a failure can leave partial uploaded artifacts;
saved predecessor files support inspection and recovery."""
import argparse
import hashlib
import json
import re
from pathlib import Path

import boto3
from botocore.config import Config
from bda_pipeline.control.release import ASSETS, _inspect, SENSITIVE_TEXT

ACCOUNT = '588738581042'
BUCKET = 'is459-g3-dashboard-588738581042-ap-southeast-1'
DATA_BUCKET = 'is459-g3-588738581042-ap-southeast-1'
DISTRIBUTION = 'EU1WB6XM4GKHQ'


def read_object(client, bucket, key):
    response = client.get_object(Bucket=bucket, Key=key)
    with response['Body'] as stream:
        data = stream.read(12_000_001)
    if len(data) > 12_000_000:
        raise ValueError('Oversized remote asset')
    return data, response['ETag']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--expected-pointer', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,35}', args.run_id) or args.evidence.exists():
        raise ValueError('Require a safe release ID and fresh evidence directory')
    manifest = json.loads((args.release / 'manifest.json').read_text())
    if {item['file'] for item in manifest['files']} != set(ASSETS):
        raise ValueError('Require exactly the four reviewed assets')
    blobs = {}
    for item in manifest['files']:
        name = item['file']
        blob = (args.release / 'site' / name).read_bytes()
        if len(blob) != item['bytes'] or len(blob) > 10_000_000 or hashlib.sha256(blob).hexdigest() != item['sha256']:
            raise ValueError('Release hash or size mismatch')
        if SENSITIVE_TEXT.search(blob.decode('utf-8')):
            raise ValueError('Private content detected')
        blobs[name] = blob
    snapshot = json.loads(blobs['dashboard.json'])
    _inspect(snapshot)
    if snapshot.get('buildStatus') != 'complete' or not snapshot.get('exploration') or sum(map(len, blobs.values())) > 12_000_000:
        raise ValueError('Incomplete or oversized enhanced release')
    session = boto3.Session(profile_name=args.profile, region_name='ap-southeast-1')
    config = Config(connect_timeout=10, read_timeout=60, retries={'mode': 'standard', 'max_attempts': 4})
    s3, sts, cloudfront = [session.client(name, config=config) for name in ['s3', 'sts', 'cloudfront']]
    if sts.get_caller_identity()['Account'] != ACCOUNT:
        raise ValueError('Wrong AWS account')
    if s3.list_objects_v2(Bucket=DATA_BUCKET, Prefix='pipeline/lock.json', MaxKeys=1)['KeyCount']:
        raise ValueError('Pipeline is running; preserve current publication')
    previous, etag = read_object(s3, BUCKET, 'dashboard/current.json')
    if json.loads(previous) != json.loads(args.expected_pointer.read_text()):
        raise ValueError('Publication changed since preparation')
    args.evidence.mkdir(parents=True)
    (args.evidence / 'previous-current.json').write_bytes(previous)
    for name in ASSETS:
        old, _ = read_object(s3, BUCKET, 'dashboard/' + name)
        (args.evidence / ('previous-' + name)).write_bytes(old)
    for name, blob in blobs.items():
        key = f'dashboard/releases/{args.run_id}/{name}'
        s3.put_object(Bucket=BUCKET, Key=key, Body=blob, IfNoneMatch='*', ContentType=ASSETS[name],
                      ServerSideEncryption='AES256', CacheControl='public,max-age=31536000,immutable')
        uploaded, _ = read_object(s3, BUCKET, key)
        if uploaded != blob:
            raise ValueError('Uploaded immutable asset differs')
    current, _ = read_object(s3, BUCKET, 'dashboard/current.json')
    if current != previous or s3.list_objects_v2(Bucket=DATA_BUCKET, Prefix='pipeline/lock.json', MaxKeys=1)['KeyCount']:
        raise ValueError('Concurrent pipeline activity; immutable release retained without switching')
    for name, blob in blobs.items():
        s3.put_object(Bucket=BUCKET, Key='dashboard/' + name, Body=blob, ContentType=ASSETS[name],
                      ServerSideEncryption='AES256', CacheControl='no-cache')
    pointer = {'schema_version': 1, 'run_id': args.run_id, 'snapshot': f'releases/{args.run_id}/dashboard.json',
               'sha256': hashlib.sha256(blobs['dashboard.json']).hexdigest(),
               'previous_run_id': json.loads(previous)['run_id'], 'source_pipeline_run_id': snapshot['pipeline']['run_id']}
    s3.put_object(Bucket=BUCKET, Key='dashboard/current.json', Body=json.dumps(pointer).encode(), IfMatch=etag,
                  ContentType='application/json', ServerSideEncryption='AES256', CacheControl='no-store,max-age=0')
    invalidation = cloudfront.create_invalidation(DistributionId=DISTRIBUTION,
        InvalidationBatch={'Paths': {'Quantity': 6, 'Items': ['/', '/index.html', '/dashboard.js', '/dashboard.css', '/dashboard.json', '/current.json']},
                           'CallerReference': 'exploration-' + args.run_id})['Invalidation']['Id']
    proof = {'status': 'published-origin-verified', 'pointer': pointer, 'invalidation': invalidation,
             'files': manifest['files'], 'model_changed': False, 'paid_batch_started': False}
    (args.evidence / 'publication.json').write_text(json.dumps(proof, indent=2))
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
