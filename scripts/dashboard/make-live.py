"""Point the main dashboard (dashboard/current.json) at an already-published release.

This command writes to AWS. Run only when someone explicitly asks for a
release to become the main dashboard. Pipeline runs never do this except for
HI-Large: every other run publishes to the demo page (dashboard/demo.json),
so this script is the deliberate "make live" step.

Checks before writing: the account is Group 3's; the release's dashboard.json
exists under dashboard/releases/<id>/, is a complete schema-1 snapshot of that
run and fits the release budget. The old pointer is saved under --evidence,
the new pointer is written with IfMatch on the old ETag (a concurrent change
fails instead of being overwritten), and /current.json is invalidated.
Use --pointer demo.json to set the demo page instead.

Example:
    python scripts/dashboard/make-live.py --run-id hi-large-delta1-20261107-batch --evidence output/make-live-<date>
"""
import argparse
import hashlib
import json
import re
import time
from pathlib import Path

import boto3
from botocore.config import Config
from bda_pipeline.release import ASSET_MAX_BYTES

ACCOUNT = '588738581042'
BUCKET = 'is459-g3-dashboard-588738581042-ap-southeast-1'
DISTRIBUTION = 'EU1WB6XM4GKHQ'
POINTERS = ('current.json', 'demo.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--pointer', choices=POINTERS, default='current.json')
    parser.add_argument('--profile', default='is459-g3-tools')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,35}', args.run_id) or args.evidence.exists():
        raise ValueError('Require a safe release ID and a fresh evidence directory')
    session = boto3.Session(profile_name=args.profile, region_name='ap-southeast-1')
    if session.client('sts').get_caller_identity()['Account'] != ACCOUNT:
        raise ValueError('Wrong AWS account')
    s3 = session.client('s3', config=Config(retries={'mode': 'standard', 'total_max_attempts': 3}))
    key = f'releases/{args.run_id}/dashboard.json'
    response = s3.get_object(Bucket=BUCKET, Key='dashboard/' + key)
    with response['Body'] as stream: data = stream.read(ASSET_MAX_BYTES['dashboard.json'] + 1)
    if not 0 < len(data) <= ASSET_MAX_BYTES['dashboard.json']: raise ValueError('Release snapshot outside the release budget')
    snapshot = json.loads(data)
    if (snapshot.get('schema_version') != 1 or snapshot.get('buildStatus') != 'complete'
            or snapshot.get('pipeline', {}).get('run_id') != args.run_id):
        raise ValueError('Release snapshot provenance mismatch')
    try:
        old = s3.get_object(Bucket=BUCKET, Key='dashboard/' + args.pointer)
        with old['Body'] as stream: previous, etag = stream.read(100_001), old['ETag']
    except s3.exceptions.NoSuchKey:
        previous, etag = None, None
    args.evidence.mkdir(parents=True)
    if previous is not None: (args.evidence / ('previous-' + args.pointer)).write_bytes(previous)
    previous_run = json.loads(previous).get('run_id') if previous else None
    if previous_run == args.run_id:
        print(f'{args.pointer} already points at {args.run_id}; nothing written.')
        return
    pointer = {'schema_version': 1, 'run_id': args.run_id, 'snapshot': key,
               'sha256': hashlib.sha256(data).hexdigest(), 'previous_run_id': previous_run}
    body = json.dumps(pointer, sort_keys=True, separators=(',', ':')).encode()
    s3.put_object(Bucket=BUCKET, Key='dashboard/' + args.pointer, Body=body, ContentType='application/json',
                  ServerSideEncryption='AES256', CacheControl='no-store,max-age=0',
                  **({'IfMatch': etag} if etag else {'IfNoneMatch': '*'}))
    (args.evidence / ('new-' + args.pointer)).write_bytes(body)
    session.client('cloudfront').create_invalidation(DistributionId=DISTRIBUTION, InvalidationBatch={
        'Paths': {'Quantity': 1, 'Items': ['/' + args.pointer]}, 'CallerReference': f'make-live-{args.run_id}-{int(time.time())}'})
    print(f'{args.pointer}: {previous_run} -> {args.run_id}. Old pointer saved in {args.evidence}.')


if __name__ == '__main__':
    main()
