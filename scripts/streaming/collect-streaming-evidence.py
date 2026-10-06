"""Read-only capture of the approved streaming run; never sends or replays events."""
import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config
from boto3.s3.transfer import TransferConfig

from bda_pipeline.streaming import account_key

RUN = 'stream-smoke-20261006'
NAME = 'is459-g3-' + RUN
BUCKET = 'is459-g3-588738581042-ap-southeast-1'
FAILURE_BUCKET = 'is459-g3-stream-failures-588738581042-ap-southeast-1'
IMAGE = 'sha256:7430b609e68fc7c3c44c3ebf158909e79ba223d6f9aaac5eaf0f57ff99697130'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['preflight', 'reconcile'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path, default=Path('output/streaming-demo-20261006'))
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Use a fresh evidence directory')
    config = Config(retries={'total_max_attempts': 2, 'mode': 'standard'}, connect_timeout=3, read_timeout=20)
    session = boto3.Session(profile_name='is459-g3-tools', region_name='ap-southeast-1')
    client = lambda service: session.client(service, config=config)
    if client('sts').get_caller_identity()['Account'] != '588738581042': raise ValueError('Wrong account')
    lam, s3, ecr = client('lambda'), client('s3'), client('ecr')
    function = lam.get_function(FunctionName=NAME)
    configuration = function['Configuration']
    assert function['Code']['ResolvedImageUri'].endswith(IMAGE)
    assert configuration['State'] == 'Active' and configuration['LastUpdateStatus'] == 'Successful'
    mappings = lam.list_event_source_mappings(FunctionName=NAME)['EventSourceMappings']
    assert len(mappings) == 1
    mapping = mappings[0]
    stream = client('kinesis').describe_stream_summary(StreamName=NAME)['StreamDescriptionSummary']
    assert stream['StreamStatus'] == 'ACTIVE' and stream['OpenShardCount'] == 1
    table = session.resource('dynamodb', config=config).Table(NAME)
    items = []
    scan_args = {'ConsistentRead': True}
    while True:
        page = table.scan(**scan_args)
        items.extend(page['Items'])
        if 'LastEvaluatedKey' not in page: break
        scan_args['ExclusiveStartKey'] = page['LastEvaluatedKey']
    def objects(prefix, bucket=BUCKET):
        return [obj for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix)
                for obj in page.get('Contents', [])]
    audit = objects('streaming/' + RUN + '/audit/')
    failures = objects('aws/lambda/' + mapping['UUID'] + '/', FAILURE_BUCKET)
    scan = ecr.describe_image_scan_findings(repositoryName='is459-g3-streaming', imageId={'imageDigest': IMAGE})
    report = {'captured_at': datetime.now(timezone.utc).isoformat(), 'phase': args.phase, 'run_id': RUN,
              'function': configuration, 'resolved_image_uri': function['Code']['ResolvedImageUri'],
              'mapping': mapping, 'stream': stream, 'table_items': items, 'audit_objects': audit,
              'failure_objects': failures, 'image_scan': scan}
    args.output.mkdir(parents=True)
    if args.phase == 'preflight':
        assert not items and not audit and not failures, 'Run namespace is not fresh'
        assert mapping['State'] == 'Disabled', 'Consumption must be disabled before replay'
    else:
        expected = {r['transaction_id']: r for r in map(json.loads, (args.package / 'results.jsonl').read_text(encoding='utf-8').splitlines())}
        counts = {}
        for event in map(json.loads, (args.package / 'events.jsonl').read_text(encoding='utf-8').splitlines()):
            txn = event['transaction']
            key = RUN + ':sender:' + account_key(txn['from_bank'], txn['from_account'])
            counts[key] = counts.get(key, 0) + 1
        states = {item['pk']: item for item in items if ':sender:' in item['pk']}
        results = {json.loads(item['payload'])['transaction_id']: json.loads(item['payload']) for item in items if ':result:' in item['pk']}
        assert len(items) == len(expected) + len(counts) and set(states) == set(counts)
        assert all(int(states[key]['version']) == count and json.loads(states[key]['payload'])['version'] == count for key, count in counts.items())
        assert set(results) == set(expected) and len(audit) == len(expected) and not failures
        audit_dir = args.output / 'audit'
        audit_dir.mkdir()
        def download_and_check(obj):
            path = audit_dir / Path(obj['Key']).name
            s3.download_file(BUCKET, obj['Key'], str(path), Config=TransferConfig(use_threads=False))
            downloaded = json.loads(path.read_text(encoding='utf-8'))
            assert downloaded == results[downloaded['transaction_id']], 'S3/DynamoDB result mismatch'
        # The already-created low-level client is thread safe; avoid a nested transfer pool.
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(download_and_check, audit))
        report['sender_versions_exact'] = True
        report['dynamodb_s3_results_exact'] = True
    (args.output / 'evidence.json').write_text(json.dumps(report, indent=2,
        default=lambda value: int(value) if isinstance(value, Decimal) else value.isoformat()) + '\n', encoding='utf-8')
    print(json.dumps({'phase': args.phase, 'items': len(items), 'audit': len(audit), 'failures': len(failures),
                      'mapping_state': mapping['State'], 'scan_status': scan['imageScanStatus']['status']}))


if __name__ == '__main__': main()
