"""Bounded sequential CSV replay. Default is a dry run with no AWS calls."""
import argparse,json,time
from pathlib import Path
import pandas as pd
from bda_pipeline.model import digest
from bda_pipeline.streaming import account_key,canonical,INPUT_COLUMNS,validate_event


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--execute-approved',action='store_true')
    parser.add_argument('--profile',default='is459-g3-tools')
    parser.add_argument('--rate',type=float,default=10)
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    if not 1<=args.rate<=20: raise ValueError('Use 1–20 events per second')
    manifest=json.loads((args.package/'verification.json').read_text(encoding='utf-8'))
    source=args.package/'replay.csv'
    if manifest['status']!='local-parity-passed-not-deployed' or digest(source)!=manifest['replay_sha256']:
        raise ValueError('Unverified or modified replay CSV')
    frame=pd.read_csv(source,float_precision='round_trip',dtype={key:'string' for key in INPUT_COLUMNS if key not in ['source_row_index','amount_usd','amount_usd_asof']})
    if len(frame)!=manifest['rows'] or not 1<=len(frame)<=2000:raise ValueError('Replay count mismatch')
    events=[]
    for row in frame.to_dict('records'):
        event={'run_id':row['run_id'],'ordinal':row['ordinal'],'transaction':{key:row[key] for key in INPUT_COLUMNS}}
        validate_event(event,manifest['run_id']);events.append(event)
    if not args.execute_approved:
        print(json.dumps({'status':'dry-run-no-AWS-calls','events':len(events),'run_id':manifest['run_id']}));return
    if args.receipt is None or args.receipt.exists(): raise ValueError('A fresh private receipt path is required')
    import boto3
    from botocore.config import Config
    session=boto3.Session(profile_name=args.profile,region_name='ap-southeast-1')
    config=Config(retries={'total_max_attempts':2,'mode':'standard'},connect_timeout=3,read_timeout=10)
    if session.client('sts',config=config).get_caller_identity()['Account']!='588738581042':raise ValueError('Wrong AWS account')
    client=session.client('kinesis',config=config)
    stream='is459-g3-'+manifest['run_id']
    summary=client.describe_stream_summary(StreamName=stream)['StreamDescriptionSummary']
    if summary['StreamStatus']!='ACTIVE' or summary['OpenShardCount']!=1:raise ValueError('Require the reviewed active one-shard stream')
    args.receipt.parent.mkdir(parents=True,exist_ok=True)
    # Open exclusively before first send; an interrupted producer must be reconciled before any resumed send.
    with args.receipt.open('x',encoding='utf-8') as receipt:
        for event in events:
            result=client.put_record(StreamName=stream,Data=canonical(event).encode(),
                PartitionKey=account_key(event['transaction']['from_bank'],event['transaction']['from_account']))
            receipt.write(canonical({'transaction_id':event['transaction']['transaction_id'],'sequence_number':result['SequenceNumber'],'shard':result['ShardId']})+'\n');receipt.flush()
            time.sleep(1/args.rate)
    print(json.dumps({'status':'sent-reconciliation-required','events':len(events),'run_id':manifest['run_id']}))


if __name__=='__main__':main()
