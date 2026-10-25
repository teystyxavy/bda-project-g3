"""Bounded sequential CSV replay. Default is a dry run with no AWS calls.

Purpose
-------
The producer of the streaming demo. It reads the verified replay package
from ``prepare-streaming-demo.py`` and sends each event, in order, to the
Kinesis Data Stream ``is459-g3-<run_id>`` (one shard). The Lambda consumer
then computes features, scores with the frozen model and stores results in
DynamoDB + an S3 audit copy.

Safety design
-------------
* **Dry run by default**: without ``--execute-approved`` it only validates
  every event and prints a count; ``boto3`` is not even imported.
* The package must still say ``local-parity-passed-not-deployed`` and the
  CSV's SHA-256 must equal the recorded hash, so an edited CSV is refused.
* Every event is checked with the same ``validate_event`` the Lambda uses
  (no labels, no precomputed rolling features, valid IDs/amounts/dates).
* The rate is capped at 1-20 events/second (well under a single shard's
  1,000 records/s write limit) and the cohort at 2,000 events.
* It checks the AWS account and that the stream is ACTIVE with exactly one
  open shard before sending.
* A *receipt* file (created exclusively, flushed after every record) logs
  each transaction's Kinesis sequence number and shard. If the producer is
  interrupted, the receipt shows exactly what was sent, so the run can be
  reconciled before any resumed send (no blind re-send).

Partition key = hashed sender account, so all of one sender's events go to
the same shard in order (required for correct rolling features).

Usage::

    python scripts/streaming/replay-streaming-demo.py --package <dir>               # dry run
    python scripts/streaming/replay-streaming-demo.py --package <dir> \\
        --execute-approved --receipt private/receipt.jsonl [--rate 10]
"""
import argparse,json,time
from pathlib import Path
import pandas as pd
from bda_pipeline.common.integrity import digest
from bda_pipeline.scoring.streaming import account_key,canonical,INPUT_COLUMNS,validate_event


def main():
    """Validate the replay package and (only if approved) send it to Kinesis.

    Side effects (execute mode only): Kinesis PutRecord calls and a new
    receipt JSONL file. Prints a JSON status line in both modes.

    Raises:
        ValueError: invalid rate, modified/unverified package, count mismatch,
            missing/existing receipt path, wrong account or unexpected stream.
    """
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--execute-approved',action='store_true')
    parser.add_argument('--profile',default='default')
    parser.add_argument('--rate',type=float,default=10)
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    # Keep the replay slow and bounded (events per second).
    if not 1<=args.rate<=20: raise ValueError('Use 1–20 events per second')
    # verification.json was written by prepare-streaming-demo.py.
    manifest=json.loads((args.package/'verification.json').read_text(encoding='utf-8'))
    source=args.package/'replay.csv'
    if manifest['status']!='local-parity-passed-not-deployed' or digest(source)!=manifest['replay_sha256']:
        raise ValueError('Unverified or modified replay CSV')
    # Read identifiers/categories as strings (so values like '0001' keep their
    # leading zeros) and floats with round-trip precision, so the JSON events
    # are identical to those verified locally. The listed numeric columns keep
    # their numeric types.
    frame=pd.read_csv(source,float_precision='round_trip',dtype={key:'string' for key in INPUT_COLUMNS if key not in ['source_row_index','amount_usd','amount_usd_asof']})
    if len(frame)!=manifest['rows'] or not 1<=len(frame)<=2000:raise ValueError('Replay count mismatch')
    events=[]
    # Rebuild each event in the exact {run_id, ordinal, transaction} shape the
    # Lambda expects and validate it with the shared contract.
    for row in frame.to_dict('records'):
        event={'run_id':row['run_id'],'ordinal':row['ordinal'],'transaction':{key:row[key] for key in INPUT_COLUMNS}}
        validate_event(event,manifest['run_id']);events.append(event)
    # Dry run stops here: nothing is sent.
    if not args.execute_approved:
        print(json.dumps({'status':'dry-run-no-AWS-calls','events':len(events),'run_id':manifest['run_id']}));return
    if args.receipt is None or args.receipt.exists(): raise ValueError('A fresh private receipt path is required')
    # boto3 is imported lazily so the dry run works without AWS libraries/credentials.
    import boto3
    from botocore.config import Config
    session=boto3.Session(profile_name=args.profile,region_name='ap-southeast-1')
    # Two attempts at most: a retried PutRecord could duplicate an event, which
    # the consumer tolerates (idempotent results) but should be rare.
    config=Config(retries={'total_max_attempts':2,'mode':'standard'},connect_timeout=3,read_timeout=10)
    if session.client('sts',config=config).get_caller_identity()['Account']!='588738581042':raise ValueError('Wrong AWS account')
    client=session.client('kinesis',config=config)
    stream='is459-g3-'+manifest['run_id']
    # Refuse to send unless the reviewed one-shard stream is active.
    summary=client.describe_stream_summary(StreamName=stream)['StreamDescriptionSummary']
    if summary['StreamStatus']!='ACTIVE' or summary['OpenShardCount']!=1:raise ValueError('Require the reviewed active one-shard stream')
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    # Open exclusively before first send; an interrupted producer must be reconciled before any resumed send.
    with args.receipt.open('x',encoding='utf-8') as receipt:
        for event in events:
            # Partition key = hashed sender, keeping each sender's events ordered on one shard.
            result=client.put_record(StreamName=stream,Data=canonical(event).encode(),
                PartitionKey=account_key(event['transaction']['from_bank'],event['transaction']['from_account']))
            # Record what was sent immediately (flush) so a crash leaves an accurate receipt.
            receipt.write(canonical({'transaction_id':event['transaction']['transaction_id'],'sequence_number':result['SequenceNumber'],'shard':result['ShardId']})+'\n');receipt.flush()
            # Throttle to the requested rate.
            time.sleep(1/args.rate)
    # Sending is not success: the consumer output must still be reconciled
    # (collect-streaming-evidence.py / verify-streaming-demo.py).
    print(json.dumps({'status':'sent-reconciliation-required','events':len(events),'run_id':manifest['run_id']}))


if __name__=='__main__':main()
