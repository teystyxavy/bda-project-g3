"""Read-only timings and stage-cache evidence for a completed ingestion run."""
import argparse
import json
from datetime import datetime
from pathlib import Path

import boto3
from botocore.config import Config

from bda_pipeline.ingest_contract import ACCOUNT, BUCKET, REGION, valid_run


def benchmark(run_id, profile, output, previous=None):
    valid_run(run_id)
    session = boto3.Session(profile_name=profile, region_name=REGION)
    sdk = Config(retries={'mode': 'standard', 'total_max_attempts': 3}, connect_timeout=10,
                 read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    states, s3 = [session.client(name, config=sdk) for name in ['stepfunctions', 's3']]
    executions = {}
    for machine in ['is459-g3-cloud-ingest', 'is459-g3-ingest']:
        arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:{machine}:{run_id}'
        execution = states.describe_execution(executionArn=arn)
        if execution['status'] != 'SUCCEEDED': raise ValueError('Benchmark requires a successful completed run')
        executions[machine] = {'start': execution['startDate'].isoformat(), 'stop': execution['stopDate'].isoformat(),
                               'seconds': (execution['stopDate'] - execution['startDate']).total_seconds()}
    report = {'run_id': run_id, 'executions': executions, 'processing_completion': {}}
    completed = {}
    for page in states.get_paginator('get_execution_history').paginate(
            executionArn=f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:{run_id}', includeExecutionData=True):
        for event in page['events']:
            entry = event.get('stateEnteredEventDetails', {})
            if entry.get('name') in ['PrepareBranches', 'Publish']:
                completed['baseline' if entry['name'] == 'PrepareBranches' else 'export'] = event['timestamp']
    sm = session.client('sagemaker', config=sdk)
    for stage, next_time in completed.items():
        job = sm.describe_processing_job(ProcessingJobName=f'is459-g3-{stage}-{run_id}')
        report['processing_completion'][stage] = {
            'compute_seconds': (job['ProcessingEndTime'] - job['ProcessingStartTime']).total_seconds(),
            'compute_end_to_next_stage_seconds': (next_time - job['ProcessingEndTime']).total_seconds(),
            'service_last_modified_to_next_stage_seconds': (next_time - job['LastModifiedTime']).total_seconds(),
            'note': 'Includes final output upload and service status propagation as well as orchestration detection.'}
    for name in ['raw/manifest.json', 'evidence/stage-cache.json', 'evidence/result.json']:
        response = s3.get_object(Bucket=BUCKET, Key=f'pipeline/runs/{run_id}/{name}')
        with response['Body'] as body: data = body.read(100_001)
        if len(data) > 100_000: raise ValueError('Oversized evidence')
        value = json.loads(data)
        report[name] = value
    if previous:
        reference = json.loads(previous.read_text())
        if 'executions' in reference:
            old_seconds = reference['executions']['is459-g3-cloud-ingest']['seconds']
        else:
            old_seconds = (datetime.fromisoformat(reference['stopDate']) - datetime.fromisoformat(reference['startDate'])).total_seconds()
        current_seconds = executions['is459-g3-cloud-ingest']['seconds']
        report['comparison'] = {'reference': str(previous), 'reference_seconds': old_seconds,
                                'seconds_saved': old_seconds - current_seconds,
                                'percent_faster': (old_seconds-current_seconds)/old_seconds*100,
                                'scope': 'One observed run per configuration; no percentile or SLA claim.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps({key: report[key] for key in ['run_id', 'executions', 'processing_completion', 'comparison'] if key in report}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    benchmark(args.run_id, args.profile, args.output, args.previous)


if __name__ == '__main__': main()
