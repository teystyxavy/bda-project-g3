"""Read-only progress and evidence for one AWS ingestion execution."""
import argparse
import json
from pathlib import Path
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from bda_pipeline.ingest_contract import ACCOUNT, BUCKET, REGION, paths, valid_run


def inspect(run_id, profile, output):
    valid_run(run_id)
    session = boto3.Session(profile_name=profile, region_name=REGION)
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3}, connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    states, glue, sm, s3 = [session.client(name, config=config) for name in ['stepfunctions', 'glue', 'sagemaker', 's3']]
    arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:{run_id}'
    cloud_arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-cloud-ingest:{run_id}'
    try:
        cloud = states.describe_execution(executionArn=cloud_arn)
    except states.exceptions.ExecutionDoesNotExist:
        cloud = None
    try:
        execution = states.describe_execution(executionArn=arn)
    except states.exceptions.ExecutionDoesNotExist:
        if cloud is None: raise
        arn, execution = cloud_arn, cloud
    report = {key: execution[key] for key in ['status', 'startDate', 'stopDate', 'error', 'cause'] if key in execution}
    report['run_id'] = run_id
    report['execution_arn'] = arn
    if cloud:
        report['cloud_execution_arn'] = cloud_arn
        report['cloud_status'] = cloud['status']
    report['states_entered'] = []
    query_ids = set()
    for page in states.get_paginator('get_execution_history').paginate(executionArn=arn, includeExecutionData=True):
        for event in page['events']:
            if 'stateEnteredEventDetails' in event:
                report['states_entered'].append({'name': event['stateEnteredEventDetails']['name'], 'time': event['timestamp']})
            details = event.get('taskSucceededEventDetails', {})
            if details.get('resourceType') == 'athena' and details.get('output'):
                result = json.loads(details['output'])
                result = result.get('QueryExecution', result)
                if result.get('QueryExecutionId'): query_ids.add(result['QueryExecutionId'])
    report['athena'] = []
    athena = session.client('athena', config=config)
    for query_id in sorted(query_ids):
        query = athena.get_query_execution(QueryExecutionId=query_id)['QueryExecution']
        report['athena'].append({key: query[key] for key in
            ['QueryExecutionId', 'Status', 'Statistics', 'ResultConfiguration', 'WorkGroup'] if key in query})
    p = paths(run_id)
    report['glue'] = []
    for name, argument, directory in [('curate', '--output', 'curated'), ('score', '--OUTPUT', 'scored')]:
        for page in glue.get_paginator('get_job_runs').paginate(JobName='is459-g3-ingest-' + name):
            for run in page['JobRuns']:
                if run.get('Arguments', {}).get(argument) == f"s3://{BUCKET}/{p[directory]}/":
                    report['glue'].append({key: run[key] for key in ['JobName', 'Id', 'JobRunState', 'ExecutionTime', 'StartedOn', 'CompletedOn', 'ErrorMessage'] if key in run})
    report['sagemaker'] = []
    for kind, stage in [('Processing', 'baseline'), ('Processing', 'export'), ('Training', 'train')]:
        name = f'is459-g3-{stage}-{run_id}'
        try:
            job = sm.describe_processing_job(ProcessingJobName=name) if kind == 'Processing' else sm.describe_training_job(TrainingJobName=name)
        except ClientError as error:
            if error.response['Error']['Code'] == 'ValidationException' and any(text in str(error) for text in ['not find', 'not found', 'does not exist']):
                continue
            raise
        fields = [kind+'JobName', kind+'JobStatus', kind+'StartTime', kind+'EndTime', 'FailureReason', 'TrainingTimeInSeconds', 'BillableTimeInSeconds', 'ModelArtifacts']
        report['sagemaker'].append({key: job[key] for key in fields if key in job})
    if 'output' in execution: report['result'] = json.loads(execution['output'])
    output.mkdir(parents=True, exist_ok=True)
    (output / 'progress.json').write_text(json.dumps(report, indent=2, default=str))
    if execution['status'] == 'SUCCEEDED' and not report.get('result', {}).get('cache_hit'):
        for suffix in ['baseline/proof.json', 'export/evidence/scores.json', 'export/evidence/corridors.json', 'export/release/manifest.json', 'evidence/result.json']:
            response = s3.get_object(Bucket=BUCKET, Key=f'pipeline/runs/{run_id}/{suffix}')
            with response['Body'] as stream: data = stream.read(1_000_001)
            if len(data) > 1_000_000: raise ValueError('Unexpected evidence size')
            (output / suffix.replace('/', '-')).write_bytes(data)
    print(json.dumps(report, indent=2, default=str))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inspect(args.run_id, args.profile, args.output)
