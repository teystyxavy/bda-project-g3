"""Write a replay package's preloaded receiver states to DynamoDB. Default is a dry run with no AWS calls.

Purpose
-------
Receiver-side features count every payment into the receiver, including
payments from senders outside the replay. ``prepare-streaming-demo.py`` takes
that activity from the verified batch data and writes it to
``receivers.jsonl``; this script stores each state once, at version 0, in the
run's DynamoDB table before any event is sent. The consumer refuses to score a
payment to a receiver that was not preloaded.

Safety design
-------------
* **Dry run by default**: without ``--execute-approved`` it only validates the
  package and prints a count; ``boto3`` is not imported.
* The package must still say ``local-parity-passed-not-deployed`` and
  ``receivers.jsonl`` must match its recorded SHA-256.
* Each item is written with ``attribute_not_exists(pk)``, so nothing already
  stored (a seeded receiver or one the consumer has advanced) is overwritten.
* It checks the AWS account and that the run's table is ACTIVE with deletion
  protection, then reads every item back with a consistent read.

Usage::

    python scripts/streaming/seed-streaming-receivers.py --package <dir>                       # dry run
    python scripts/streaming/seed-streaming-receivers.py --package <dir> --execute-approved [--profile is459-g3]
"""
import argparse
import importlib.util
import json
from pathlib import Path

from bda_pipeline.model import digest
from bda_pipeline.streaming import canonical, empty_scan


def load(package):
    """Return (run_id, {pk: state}) after checking the package status and receivers digest."""
    manifest = json.loads((package / 'verification.json').read_text(encoding='utf-8'))
    path = package / 'receivers.jsonl'
    if manifest['status'] != 'local-parity-passed-not-deployed' or digest(path) != manifest.get('receivers_sha256'):
        raise ValueError('Unverified or modified receiver preload')
    run_id = manifest['run_id']
    seeds = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        state = row['state']
        # A preloaded receiver is a fresh scan (version 0) holding only pending payments in scan order.
        if (not row['pk'].startswith(run_id + ':receiver:') or row['pk'] in seeds
                or {key: value for key, value in state.items() if key != 'pending'} != {'version': 0, **empty_scan()}
                or not isinstance(state.get('pending'), list) or state['pending'] != sorted(state['pending'], key=lambda entry: entry[:2])):
            raise ValueError('Unexpected receiver preload row')
        seeds[row['pk']] = state
    if len(seeds) != manifest['receivers']:
        raise ValueError('Receiver preload count mismatch')
    return run_id, seeds


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--execute-approved', action='store_true')
    parser.add_argument('--profile', default='is459-g3-tools')
    args = parser.parse_args()
    run_id, seeds = load(args.package)
    if not args.execute_approved:
        print(json.dumps({'status': 'dry-run-no-AWS-calls', 'receivers': len(seeds), 'run_id': run_id}))
        return
    import boto3
    from botocore.config import Config
    session = boto3.Session(profile_name=args.profile, region_name='ap-southeast-1')
    config = Config(retries={'total_max_attempts': 3, 'mode': 'standard'}, connect_timeout=3, read_timeout=10)
    if session.client('sts', config=config).get_caller_identity()['Account'] != '588738581042':
        raise ValueError('Wrong AWS account')
    table = 'is459-g3-' + run_id
    summary = session.client('dynamodb', config=config).describe_table(TableName=table)['Table']
    if summary['TableStatus'] != 'ACTIVE' or not summary.get('DeletionProtectionEnabled'):
        raise ValueError('Require the reviewed active, deletion-protected state table')
    # Reuse the consumer's own DynamoDB adapter so items are stored exactly as the Lambda reads them.
    spec = importlib.util.spec_from_file_location('stream_handler', Path(__file__).resolve().parents[2] / 'streaming/handler.py')
    handler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handler)
    ddb = session.resource('dynamodb', config=config).meta.client
    store = handler.DynamoStore(ddb, None, table, None, None)
    for key, state in seeds.items():
        store.preload(key, state)
    # Read back: every receiver exists, unchanged, at version 0.
    for key, state in seeds.items():
        stored = store.state(key)
        if stored is None or canonical(stored) != canonical(state):
            raise ValueError('Preloaded receiver did not read back unchanged')
    print(json.dumps({'status': 'receivers-preloaded', 'receivers': len(seeds), 'run_id': run_id, 'table': table}))


if __name__ == '__main__':
    main()
