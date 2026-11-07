"""Read-only capture of the approved streaming run; never sends or replays events.

Purpose
-------
Evidence collector for the optional streaming demo (Kinesis -> Lambda ->
DynamoDB, with an S3 audit copy). It is run twice around the single
approved replay:

* ``--phase preflight`` *before* sending events: proves the deployed stack
  is exactly the reviewed one (Lambda image digest, one-shard ACTIVE
  stream, a single event-source mapping that is still **Disabled**) and that
  the run's namespace is fresh: no DynamoDB items other than the preloaded
  receiver states of ``--package/receivers.jsonl`` (exactly as prepared, all
  at version 0), no audit objects and no failure-archive objects. This
  prevents mixing a new replay with leftovers.
* ``--phase reconcile`` *after* the replay: proves every expected event was
  processed exactly once. DynamoDB must hold one ``<run>:sender:<hash>``
  state item per sender (whose version equals that sender's event count),
  one ``<run>:receiver:<hash>`` item per preloaded receiver (whose version
  equals the number of replayed payments into it) and one
  ``<run>:result:<hash>`` item per transaction; every S3 audit JSON must
  be byte-for-byte equal (as parsed JSON) to the matching DynamoDB result;
  and the Lambda failure archive must be empty.

AWS resources read (all named ``is459-g3-<run_id>``; default run
``stream-smoke-20261006``, which had no receiver preload):
Lambda function + event-source mapping, Kinesis stream summary, DynamoDB
table (consistent full scan), S3 audit prefix
``s3://<BUCKET>/streaming/<RUN>/audit/``, S3 failure bucket prefix
``aws/lambda/<mapping UUID>/``, and the ECR image scan findings. Required
IAM is read-only (Get/List/Describe/Scan/GetObject).

Local inputs: ``--package`` is the folder produced by
``prepare-streaming-demo.py`` (``events.jsonl``, ``results.jsonl`` and,
when present, ``receivers.jsonl``).
Outputs: ``--output/evidence.json`` (and ``--output/audit/*.json`` copies in
the reconcile phase). The output directory must not already exist.

Usage::

    python scripts/streaming/collect-streaming-evidence.py --phase preflight --output out/pre
    python scripts/streaming/collect-streaming-evidence.py --phase reconcile --output out/post
    python scripts/streaming/collect-streaming-evidence.py --phase preflight --run-id stream-v2-20261107 \
        --image sha256:<digest> --package output/streaming-demo-v2-20261107 --profile is459-g3 --output out/pre
"""
import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.config import Config
from boto3.s3.transfer import TransferConfig

# account_key = SHA-256 of [bank without leading zeros, account]; the same
# hashed sender key the Lambda uses in its DynamoDB partition keys.
from bda_pipeline.streaming import account_key

# Identifiers of the first reviewed streaming run (the defaults). A later
# approved run passes its own --run-id and reviewed --image digest.
RUN = 'stream-smoke-20261006'
# Private project bucket (audit copies) and the separate on-failure bucket where
# Lambda's event-source mapping archives records it could not process.
BUCKET = 'is459-g3-588738581042-ap-southeast-1'
FAILURE_BUCKET = 'is459-g3-stream-failures-588738581042-ap-southeast-1'
# Exact container image digest that was reviewed; the deployed function must match it.
IMAGE = 'sha256:7430b609e68fc7c3c44c3ebf158909e79ba223d6f9aaac5eaf0f57ff99697130'


def preloaded(package):
    """{pk: state} from the package's receivers.jsonl, or {} for a run without receiver preload."""
    path = package / 'receivers.jsonl'
    if not path.exists():
        return {}
    return {line['pk']: line['state'] for line in map(json.loads, path.read_text(encoding='utf-8').splitlines())}


def main():
    """Run the preflight or reconcile capture and write ``evidence.json``.

    Raises:
        ValueError: output directory exists or the wrong AWS account is used.
        AssertionError: any deployment, freshness or reconciliation check fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['preflight', 'reconcile'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path, default=Path('output/streaming-demo-20261006'))
    parser.add_argument('--run-id', default=RUN)
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--profile', default='is459-g3-tools')
    args = parser.parse_args()
    run, name, image = args.run_id, 'is459-g3-' + args.run_id, args.image
    seeds = preloaded(args.package)
    if any(not key.startswith(run + ':receiver:') for key in seeds): raise ValueError('Package receivers belong to another run')
    # Never overwrite earlier evidence.
    if args.output.exists(): raise ValueError('Use a fresh evidence directory')
    # At most 2 attempts and short timeouts: fail fast rather than hang.
    config = Config(retries={'total_max_attempts': 2, 'mode': 'standard'}, connect_timeout=3, read_timeout=20)
    session = boto3.Session(profile_name=args.profile, region_name='ap-southeast-1')
    # Small factory so every client shares the same session and retry config.
    client = lambda service: session.client(service, config=config)
    # Guard against running with credentials for some other AWS account.
    if client('sts').get_caller_identity()['Account'] != '588738581042': raise ValueError('Wrong account')
    lam, s3, ecr = client('lambda'), client('s3'), client('ecr')
    # The function must run the reviewed image and be fully deployed (not mid-update).
    function = lam.get_function(FunctionName=name)
    configuration = function['Configuration']
    assert function['Code']['ResolvedImageUri'].endswith(image)
    assert configuration['State'] == 'Active' and configuration['LastUpdateStatus'] == 'Successful'
    # Exactly one Kinesis -> Lambda event-source mapping is expected.
    mappings = lam.list_event_source_mappings(FunctionName=name)['EventSourceMappings']
    assert len(mappings) == 1
    mapping = mappings[0]
    # The demo uses a single provisioned shard so all events keep one global order.
    stream = client('kinesis').describe_stream_summary(StreamName=name)['StreamDescriptionSummary']
    assert stream['StreamStatus'] == 'ACTIVE' and stream['OpenShardCount'] == 1
    # Full scan with ConsistentRead so items written just before are included.
    # DynamoDB returns at most 1 MB per page; follow LastEvaluatedKey to the end.
    table = session.resource('dynamodb', config=config).Table(name)
    items = []
    scan_args = {'ConsistentRead': True}
    while True:
        page = table.scan(**scan_args)
        items.extend(page['Items'])
        if 'LastEvaluatedKey' not in page: break
        scan_args['ExclusiveStartKey'] = page['LastEvaluatedKey']
    # List every object under a prefix, following pagination.
    def objects(prefix, bucket=BUCKET):
        return [obj for page in s3.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=prefix)
                for obj in page.get('Contents', [])]
    # Audit copies written by the Lambda, and records archived by the
    # mapping's on-failure destination (keyed by the mapping UUID).
    audit = objects('streaming/' + run + '/audit/')
    failures = objects('aws/lambda/' + mapping['UUID'] + '/', FAILURE_BUCKET)
    # Vulnerability scan results of the exact image, recorded for the report.
    repository = function['Code']['ImageUri'].split('/', 1)[1].split('@')[0].split(':')[0]
    scan = ecr.describe_image_scan_findings(repositoryName=repository, imageId={'imageDigest': image})
    report = {'captured_at': datetime.now(timezone.utc).isoformat(), 'phase': args.phase, 'run_id': run,
              'function': configuration, 'resolved_image_uri': function['Code']['ResolvedImageUri'],
              'mapping': mapping, 'stream': stream, 'table_items': items, 'audit_objects': audit,
              'failure_objects': failures, 'image_scan': scan}
    args.output.mkdir(parents=True)
    # Preflight: the namespace must be empty and consumption disabled, so the
    # replay can be enabled deliberately and counted from zero.
    receivers = {item['pk']: item for item in items if ':receiver:' in item['pk']}
    if args.phase == 'preflight':
        # Only the preloaded receivers may exist, unchanged and still at version 0.
        assert set(receivers) == set(seeds) and len(items) == len(seeds), 'Run namespace is not fresh'
        assert all(int(item['version']) == 0 and json.loads(item['payload']) == seeds[key]
                   for key, item in receivers.items()), 'Preloaded receivers differ from the package'
        assert not audit and not failures, 'Run namespace is not fresh'
        assert mapping['State'] == 'Disabled', 'Consumption must be disabled before replay'
    # Reconcile: rebuild the expected state from the locally prepared package.
    else:
        expected = {r['transaction_id']: r for r in map(json.loads, (args.package / 'results.jsonl').read_text(encoding='utf-8').splitlines())}
        # Expected number of events per hashed sender = expected final state version.
        counts, arrivals = {}, {}
        for event in map(json.loads, (args.package / 'events.jsonl').read_text(encoding='utf-8').splitlines()):
            txn = event['transaction']
            key = run + ':sender:' + account_key(txn['from_bank'], txn['from_account'])
            counts[key] = counts.get(key, 0) + 1
            if seeds:
                key = run + ':receiver:' + account_key(txn['to_bank'], txn['to_account'])
                arrivals[key] = arrivals.get(key, 0) + 1
        # Split the scanned items by key type: sender state vs. per-transaction result.
        states = {item['pk']: item for item in items if ':sender:' in item['pk']}
        results = {json.loads(item['payload'])['transaction_id']: json.loads(item['payload']) for item in items if ':result:' in item['pk']}
        # Item count = one result per transaction + one state per sender and per preloaded receiver, nothing extra.
        assert len(items) == len(expected) + len(counts) + len(arrivals) and set(states) == set(counts)
        assert set(receivers) == set(arrivals) == set(seeds)
        # Each receiver applied every replayed payment into it exactly once.
        assert all(int(receivers[key]['version']) == count and json.loads(receivers[key]['payload'])['version'] == count
                   for key, count in arrivals.items())
        # Each sender's version (attribute and inside the payload) must equal its event count:
        # every event was applied once and none was skipped or double-applied.
        assert all(int(states[key]['version']) == count and json.loads(states[key]['payload'])['version'] == count for key, count in counts.items())
        # Every expected result exists, there is one audit object per result, and no failures.
        assert set(results) == set(expected) and len(audit) == len(expected) and not failures
        audit_dir = args.output / 'audit'
        audit_dir.mkdir()
        def download_and_check(obj):
            """Download one audit object and assert it equals the DynamoDB result.

            Runs in a worker thread. ``use_threads=False`` stops boto3 from
            starting its own transfer thread pool inside each worker.
            """
            path = audit_dir / Path(obj['Key']).name
            s3.download_file(BUCKET, obj['Key'], str(path), Config=TransferConfig(use_threads=False))
            downloaded = json.loads(path.read_text(encoding='utf-8'))
            assert downloaded == results[downloaded['transaction_id']], 'S3/DynamoDB result mismatch'
        # The already-created low-level client is thread safe; avoid a nested transfer pool.
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(download_and_check, audit))
        report['sender_versions_exact'] = True
        report['receiver_versions_exact'] = bool(seeds)
        report['dynamodb_s3_results_exact'] = True
    # Decimal (how DynamoDB returns numbers) -> int; datetimes -> ISO strings.
    (args.output / 'evidence.json').write_text(json.dumps(report, indent=2,
        default=lambda value: int(value) if isinstance(value, Decimal) else value.isoformat()) + '\n', encoding='utf-8')
    # Short console summary for the operator.
    print(json.dumps({'phase': args.phase, 'items': len(items), 'audit': len(audit), 'failures': len(failures),
                      'mapping_state': mapping['State'], 'scan_status': scan['imageScanStatus']['status']}))


if __name__ == '__main__': main()
