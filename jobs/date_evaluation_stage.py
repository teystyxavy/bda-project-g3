"""Pinned isolated baseline/export for a verified date-range evaluation. No training."""
import json
import shutil
import tempfile
from pathlib import Path
import boto3
from awsglue.utils import getResolvedOptions
from pyspark import SparkContext, SparkFiles
from bda_pipeline.ingest_contract import BUCKET, REGION, paths, sha, validate_manifest
from bda_pipeline.selected_population import validate_selection
from bda_pipeline.glue_compute import download_input


def main():
    import sys
    args = getResolvedOptions(sys.argv, ['RUN_ID','STAGE','CONFIG_KEY','DEPLOYMENT_SHA256'])
    stage, run_id = args['STAGE'], args['RUN_ID']
    if stage not in ['baseline','export']: raise ValueError('Invalid evaluation stage')
    s3=boto3.client('s3',region_name=REGION); p=paths(run_id)
    config=json.loads(s3.get_object(Bucket=BUCKET,Key=args['CONFIG_KEY'])['Body'].read())
    if sha(config)!=args['DEPLOYMENT_SHA256']: raise ValueError('Pinned evaluation config mismatch')
    prefix=p[stage]+'/'
    if s3.list_objects_v2(Bucket=BUCKET,Prefix=prefix,MaxKeys=1).get('KeyCount',0): raise ValueError('Fresh evaluation output required')
    SparkContext.getOrCreate()
    with tempfile.TemporaryDirectory(prefix='date-eval-') as scratch:
        root=Path(scratch);inputs=root/'inputs';output=root/'output';assets=root/'assets';assets.mkdir()
        for name in ['index.html','dashboard.js','dashboard.css']:
            source=Path(name)
            if not source.is_file():source=Path(SparkFiles.get(name))
            shutil.copyfile(source,assets/name)
        download_input(s3,p['raw']+'/manifest.json',inputs/'run')
        manifest=json.loads((inputs/'run/manifest.json').read_text());validate_manifest(manifest,sha(config));validate_selection(manifest)
        if manifest['run_id']!=run_id or manifest['model_manifest_sha256']!=config['model_manifest_sha256']: raise ValueError('Evaluation identity mismatch')
        download_input(s3,config['model_prefix']+'/',inputs/'model')
        if stage=='baseline':
            for suffix in ['manifest.json','proof.json','training-dashboard.json']:
                download_input(s3,p['evidence']+'/source/'+suffix,inputs/'source')
            # Verify copied fragments against the launcher receipt before trusting source proof.
            inventory=json.loads(s3.get_object(Bucket=BUCKET,Key=p['evidence']+'/selection-inputs.json')['Body'].read())
            if inventory['source_manifest_sha256']!=manifest['selection']['source_manifest_sha256']:raise ValueError('Selection source mismatch')
            for item in inventory['objects']:
                head=s3.head_object(Bucket=BUCKET,Key=item['destination_key'])
                if head['ETag']!=item['destination_etag'] or head['ContentLength']!=item['bytes'] or head.get('ServerSideEncryption')!='AES256':raise ValueError('Selected fragment changed')
            download_input(s3,p['curated']+'/',inputs/'curated')
            from bda_pipeline.date_evaluation import build_baseline
            build_baseline(inputs,output,assets)
        else:
            for key,target in [(p['baseline']+'/baseline/bq1_scored.parquet','baseline/baseline'),(p['baseline']+'/manifest/','baseline/manifest'),(p['scored']+'/','scored'),(p['network']+'/','network')]:download_input(s3,key,inputs/target)
            # BQ2 independently reconciles the complete source, including rows outside BQ1 evaluation.
            source_key=manifest['selection']['source_curated_prefix']
            if source_key != 'pipeline/runs/'+manifest['selection']['source_run_id']+'/curated/':raise ValueError('Unexpected network reference source')
            download_input(s3,source_key,inputs/'network_reference')
            from bda_pipeline.ingest_compute import export_stage
            export_stage(inputs,output,assets)
        for file in sorted(output.rglob('*')):
            if file.is_file():s3.upload_file(str(file),BUCKET,prefix+file.relative_to(output).as_posix(),ExtraArgs={'ServerSideEncryption':'AES256'})
        s3.put_object(Bucket=BUCKET,Key=prefix+'_SUCCESS',Body=b'',ServerSideEncryption='AES256')

if __name__=='__main__':main()
