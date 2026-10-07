"""Read-only check of private proof, public release hashes, pointer and run cache.

Purpose
-------
Final acceptance of a dashboard release published by the ingestion pipeline.
For one run ID it proves that what the public sees through CloudFront is
exactly what the pipeline verified and published:

1. Private evidence in the data bucket (``pipeline/runs/<run>/``): the
   release ``manifest.json`` and ``evidence/result.json`` exist, say
   ``published``, and list exactly the four release files
   (``index.html``, ``dashboard.js``, ``dashboard.css``, ``dashboard.json``).
2. For each file, the private copy in the dashboard bucket
   (``dashboard/releases/<run>/<file>``) and the public copy downloaded over
   HTTPS have the byte size and SHA-256 recorded in the manifest.
3. The ``dashboard/current.json`` pointer is identical privately and
   publicly. If it selects this run, its hash matches this release's
   ``dashboard.json`` and the root-level HTML/JS/CSS shell served by
   CloudFront matches this release.
4. The stage cache entry ``pipeline/cache/<processing_sha256>.json`` records
   this run as published (so a later identical request can reuse it).
5. The global lock ``pipeline/lock.json`` is not still held by this run
   (a successful run must release it).

AWS access: read-only (sts:GetCallerIdentity, s3:GetObject, s3:ListBucket),
plus anonymous HTTPS GETs of the public CloudFront URLs.
Output: ``<output>/public-release-verification.json``; also printed.

Usage::

    python scripts/validation/verify-ingestion-release.py --run-id <run> --output output/release-check
"""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

import boto3
from botocore.config import Config
# Shared constants: account, region, private data bucket, dashboard site bucket.
from bda_pipeline.ingest_contract import ACCOUNT, BUCKET, REGION, SITE_BUCKET, valid_run


def verify(run_id, profile, output):
    """Run all release checks for ``run_id`` and write the report.

    Args:
        run_id: pipeline run ID (validated before use in S3 keys).
        profile: AWS CLI profile for read-only credentials.
        output: folder for ``public-release-verification.json``.

    Raises:
        AssertionError / ValueError: if any check fails.
    """
    valid_run(run_id)
    session = boto3.Session(profile_name=profile, region_name=REGION)
    # Standard retries (max 3 attempts) and short timeouts.
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3},
                    connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    # Make sure the credentials belong to the project account.
    assert session.client('sts', config=config).get_caller_identity()['Account'] == ACCOUNT
    s3 = session.client('s3', config=config)

    def read(bucket, key):
        """Return an S3 object's bytes, refusing anything over 2 MB.

        Reading at most 2,000,001 bytes and checking the length bounds memory
        use and detects unexpectedly large objects.
        """
        response = s3.get_object(Bucket=bucket, Key=key)
        with response['Body'] as stream: data = stream.read(2_000_001)
        if len(data) > 2_000_000: raise ValueError('Unexpected verification document size')
        return data

    prefix = f'pipeline/runs/{run_id}'
    manifest = json.loads(read(BUCKET, f'{prefix}/export/release/manifest.json'))
    result = json.loads(read(BUCKET, f'{prefix}/evidence/result.json'))
    assert result['status'] == 'published' and result['run_id'] == run_id
    assert manifest['pipeline_run_id'] == run_id
    assert set(item['file'] for item in manifest['files']) == {
        'index.html', 'dashboard.js', 'dashboard.css', 'dashboard.json'}
    # CloudFront base URL of this release's folder (strip the trailing index.html).
    base = result['release_url'].removesuffix('index.html')
    checked = []
    for item in manifest['files']:
        # Private (S3) and public (CloudFront) bytes must both match the manifest.
        name = item['file']
        private = read(SITE_BUCKET, f'dashboard/releases/{run_id}/{name}')
        with urlopen(base + name, timeout=30) as response: public = response.read(2_000_001)
        assert len(private) == item['bytes'] and len(public) == item['bytes']
        assert hashlib.sha256(private).hexdigest() == hashlib.sha256(public).hexdigest() == item['sha256']
        checked.append({'file': name, 'bytes': len(public), 'sha256': item['sha256']})
        if name == 'dashboard.json': snapshot = json.loads(public)
    # The "current release" pointer the dashboard shell reads to find its data.
    pointer_bytes = read(SITE_BUCKET, 'dashboard/current.json')
    pointer = json.loads(pointer_bytes)
    with urlopen(result['dashboard_url'] + 'current.json', timeout=30) as response: public_pointer = response.read(100_001)
    assert pointer_bytes == public_pointer
    selected = pointer['run_id'] == run_id
    # Only when this run is the live release do the root-level files have to match it.
    if selected:
        assert pointer['sha256'] == next(item['sha256'] for item in checked if item['file'] == 'dashboard.json')
        for item in checked:
            if item['file'] == 'dashboard.json': continue
            with urlopen(result['dashboard_url'] + item['file'], timeout=30) as response:
                root_asset = response.read(2_000_001)
            assert hashlib.sha256(root_asset).hexdigest() == item['sha256'], 'Root shell differs from selected release'
    # Successful runs are cached by their processing hash for later reuse.
    cache = json.loads(read(BUCKET, f"pipeline/cache/{result['processing_sha256']}.json"))
    assert cache['run_id'] == run_id and cache['status'] == 'published'
    # The lock prevents two pipeline runs at once; check it exists before reading.
    lock_key = 'pipeline/lock.json'
    lock = s3.list_objects_v2(Bucket=BUCKET, Prefix=lock_key, MaxKeys=1)
    lock_exists = any(item['Key'] == lock_key for item in lock.get('Contents', []))
    lock_owner = json.loads(read(BUCKET, lock_key))['run_id'] if lock_exists else None
    assert lock_owner != run_id, 'Successful run retained its lock'
    report = {'status': 'passed', 'run_id': run_id, 'selected_release': selected,
              'root_shell_verified': selected,
              'public_files': checked, 'successful_manifest_cached': True,
              'global_lock_exists': lock_exists, 'global_lock_owner': lock_owner,
              'pipeline': snapshot['pipeline'],
              'verification': manifest['verification'], 'result': result}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'public-release-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    verify(args.run_id, args.profile, args.output)
