"""Stage retained raw inputs for the standard ingestion workflow; no local CSV download.

Preparation uses conditional S3 copies and a fresh run prefix. --start requires
that the supplied deployment is already active. No workflow, worker, model or
source file is edited by this helper.
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'src'))
from bda_pipeline.ingest_contract import ACCOUNT,BUCKET,REGION,MACHINE,canonical,sha,paths,valid_run,validate_manifest


def stage(args):
    import boto3
    from boto3.s3.transfer import TransferConfig
    session=boto3.Session(profile_name=args.profile,region_name=REGION)
    if session.client('sts').get_caller_identity()['Account']!=ACCOUNT:raise ValueError('Wrong AWS account')
    s3=session.client('s3');states=session.client('stepfunctions')
    config=json.loads(args.deployment.read_text())
    source_id=valid_run(args.source_run);source=paths(source_id)
    original=json.loads(s3.get_object(Bucket=BUCKET,Key=source['raw']+'/manifest.json')['Body'].read())
    validate_manifest(original,original['deployment_sha256'])
    if original['source_scope']!='selected-subset':raise ValueError('A complete retained source subset is required')
    run_id=valid_run(args.run_id or original['subset'].lower()+'-'+datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y%m%d-%H%M%S'))
    destination=paths(run_id)
    manifest={**original,'run_id':run_id,'train':False,'rerun':False,'export_dates':None,
        'deployment_sha256':sha(config),'model_manifest_sha256':config['model_manifest_sha256'],
        'created_at':datetime.now(timezone.utc).isoformat()}
    identity={k:manifest[k] for k in ['subset','rows','files','start_date','end_date','dataset_version','deployment_sha256','train','export_dates']}
    manifest['processing_sha256']=sha(identity);validate_manifest(manifest,sha(config))
    output=Path('output')/run_id;output.mkdir(exist_ok=False)
    s3.put_object(Bucket=BUCKET,Key=destination['raw']+'/upload-claim.json',IfNoneMatch='*',Body=canonical({'run_id':run_id,'source_run_id':source_id}),ServerSideEncryption='AES256')
    transfer=TransferConfig(multipart_threshold=64*1024**2,multipart_chunksize=128*1024**2,max_concurrency=8)
    def copy(name):
        key=source['raw']+'/'+name;info=manifest['files'][name]
        before=s3.head_object(Bucket=BUCKET,Key=key)
        if before['ContentLength']!=info['bytes'] or before.get('Metadata',{}).get('sha256')!=info['sha256'] or before.get('ServerSideEncryption')!='AES256':raise ValueError('Retained raw input contract mismatch')
        s3.copy({'Bucket':BUCKET,'Key':key},BUCKET,destination['raw']+'/'+name,ExtraArgs={'CopySourceIfMatch':before['ETag'],'ServerSideEncryption':'AES256'},Config=transfer)
        after=s3.head_object(Bucket=BUCKET,Key=destination['raw']+'/'+name)
        if (after['ContentLength']!=info['bytes'] or after.get('Metadata',{}).get('sha256')!=info['sha256']
                or after.get('ServerSideEncryption')!='AES256' or s3.head_object(Bucket=BUCKET,Key=key)['ETag']!=before['ETag']):raise ValueError('Raw copy changed or incomplete')
        print('Copied and verified '+name,flush=True)
        return {'file':name,'source_etag':before['ETag'],'destination_etag':after['ETag'],'bytes':info['bytes'],'sha256':info['sha256']}
    with ThreadPoolExecutor(max_workers=3) as pool:copies=list(pool.map(copy,manifest['files']))
    s3.put_object(Bucket=BUCKET,Key=destination['raw']+'/manifest.json',IfNoneMatch='*',Body=canonical(manifest),ServerSideEncryption='AES256',ContentType='application/json')
    receipt={'run_id':run_id,'source_run_id':source_id,'subset':manifest['subset'],'rows':manifest['rows'],'status':'prepared-not-started','deployment_sha256':sha(config),'raw_copies':copies,'training_requested':False}
    (output/'manifest.json').write_bytes(canonical(manifest));(output/'preparation.json').write_bytes(canonical(receipt))
    if args.start:
        active=json.loads(s3.get_object(Bucket=BUCKET,Key='pipeline/deployment.json')['Body'].read())
        if sha(active)!=sha(config):raise ValueError('Supplied deployment is not active; inputs preserved without starting compute')
        response=states.start_execution(stateMachineArn=MACHINE,name=run_id,input=json.dumps({'run_id':run_id}))
        receipt.update(status='submitted',execution_arn=response['executionArn'])
        (output/'submission.json').write_bytes(canonical(receipt))
    print(json.dumps(receipt,indent=2),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',default='is459-g3-tools');parser.add_argument('--source-run',required=True)
    parser.add_argument('--deployment',type=Path,required=True);parser.add_argument('--run-id');parser.add_argument('--start',action='store_true')
    stage(parser.parse_args())
