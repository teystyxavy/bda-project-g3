"""Read-only source manifest and wrapper evidence for cloud ingestion."""
import argparse
import hashlib
import io
import json
from datetime import date, timedelta
from pathlib import Path

import boto3
import pandas as pd
from botocore.config import Config
from bda_pipeline.acquisition import validate_fx
from bda_pipeline.ingest_contract import ACCOUNT, BUCKET, REGION, valid_run


def inspect(run_id, output, profile):
    valid_run(run_id)
    session = boto3.Session(profile_name=profile, region_name=REGION)
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3},
                    connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    states, s3, function = [session.client(name, config=config) for name in ['stepfunctions', 's3', 'lambda']]
    arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-cloud-ingest:{run_id}'
    execution = states.describe_execution(executionArn=arn)
    report = {key: execution[key] for key in ['executionArn', 'status', 'startDate', 'stopDate', 'error', 'cause'] if key in execution}
    report['events'] = []
    for page in states.get_paginator('get_execution_history').paginate(executionArn=arn, includeExecutionData=True):
        for event in page['events']:
            item = {'type': event['type'], 'timestamp': event['timestamp']}
            for key in ['stateEnteredEventDetails', 'lambdaFunctionSucceededEventDetails',
                        'lambdaFunctionFailedEventDetails', 'lambdaFunctionTimedOutEventDetails',
                        'taskSucceededEventDetails', 'taskFailedEventDetails']:
                if key in event: item[key] = event[key]
            report['events'].append(item)
    live = function.get_function(FunctionName='is459-g3-acquire')
    report['function'] = {key: live['Configuration'][key] for key in
                          ['State', 'LastUpdateStatus', 'MemorySize', 'Timeout', 'EphemeralStorage', 'LastModified']}
    report['image_uri'] = live['Code']['ResolvedImageUri']
    for name, key in [('raw_manifest', f'pipeline/runs/{run_id}/raw/manifest.json'),
                      ('acquisition_failure', f'pipeline/runs/{run_id}/evidence/acquisition-failure.json')]:
        listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
        if not any(item['Key'] == key for item in listing.get('Contents', [])): continue
        response = s3.get_object(Bucket=BUCKET, Key=key)
        with response['Body'] as body: content = body.read(100_001)
        if len(content) > 100_000: raise ValueError('Unexpected acquisition evidence size')
        report[name] = json.loads(content)
    if 'raw_manifest' in report:
        version = report['raw_manifest']['dataset_version']
        prefix = f'pipeline/acquisition/versions/{version}'
        response = s3.get_object(Bucket=BUCKET, Key=f'{prefix}/record.json')
        with response['Body'] as body: content = body.read(100_001)
        if len(content) > 100_000: raise ValueError('Unexpected source cache record size')
        report['source_cache'] = json.loads(content)
        report['source_objects'] = {}
        for name, digest in report['source_cache']['files'].items():
            if name not in ['HI-Small_Trans.csv', 'HI-Small_accounts.csv']:
                raise ValueError('Unexpected cached source file')
            head = s3.head_object(Bucket=BUCKET, Key=f'{prefix}/{name}')
            assert head['Metadata']['sha256'] == digest
            assert head['ServerSideEncryption'] == 'AES256'
            local = Path('dataset') / name
            if local.is_file():
                with local.open('rb') as stream: local_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            else: local_digest = None
            report['source_objects'][name] = {'bytes': head['ContentLength'], 'sha256': digest,
                'encrypted': True, 'matches_retained_local_source': local_digest == digest if local_digest else None}
        manifest = report['raw_manifest']
        response = s3.get_object(Bucket=BUCKET, Key=f'pipeline/runs/{run_id}/raw/fx.csv')
        with response['Body'] as body: rates_bytes = body.read(1_000_001)
        assert len(rates_bytes) == manifest['files']['fx.csv']['bytes'] < 1_000_000
        assert hashlib.sha256(rates_bytes).hexdigest() == manifest['files']['fx.csv']['sha256']
        rates = pd.read_csv(io.BytesIO(rates_bytes))
        validate_fx(rates, date.fromisoformat(manifest['start_date']) - timedelta(days=1),
                    date.fromisoformat(manifest['end_date']), manifest['currencies'])
        report['fx_verified'] = {'status': 'passed', 'rows': len(rates),
            'sources': {source: sorted(group.currency.unique().tolist()) for source, group in rates.groupby('source')},
            'sha256': hashlib.sha256(rates_bytes).hexdigest(),
            'start': str(rates.txn_date.min()), 'end': str(rates.txn_date.max())}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'acquisition.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'input_committed': 'raw_manifest' in report,
                      'failure': report.get('acquisition_failure'),
                      'latest_event': report['events'][-1] if report['events'] else None}, default=str))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    args = parser.parse_args()
    inspect(args.run_id, args.output, args.profile)
