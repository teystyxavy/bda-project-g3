"""Read-only progress and evidence for one AWS ingestion execution.

Where this sits in the pipeline
-------------------------------
A monitoring/debugging companion to ``scripts/ingestion/ingest.py``. After a
run has been started (or has finished) the team runs this to collect, in one
JSON report, everything AWS knows about that run: the Step Functions state
history, the Athena corridor queries it ran (BQ2), the Glue curate/score job
runs, and the SageMaker baseline/export processing jobs and training job
(BQ1 scoring). It never starts, stops or changes anything.

Reads
-----
* Step Functions executions ``is459-g3-ingest:<run_id>`` (batch) and
  ``is459-g3-cloud-ingest:<run_id>`` (cloud acquisition wrapper, if any).
* Athena ``GetQueryExecution`` for every query ID found in the history.
* Glue job runs of ``is459-g3-ingest-curate`` and ``is459-g3-ingest-score``.
* SageMaker jobs ``is459-g3-{baseline,export,train}-<run_id>``.
* On success, small evidence objects under ``s3://<BUCKET>/pipeline/runs/<run_id>/``.

Writes
------
``<output>/progress.json`` plus one local copy of each evidence file (with
``/`` replaced by ``-`` in the name), and prints the report.

Usage
-----
    python scripts/ingestion/inspect-ingestion.py \
        --run-id 20261006-101500-abcd1234 --output output/inspect/<run_id>
"""
import argparse
import json
from pathlib import Path
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from bda_pipeline.common.ingest_contract import ACCOUNT, BUCKET, REGION, paths, valid_run


def inspect(run_id, profile, output):
    """Collect progress and evidence for one run and save it under ``output``.

    Args:
        run_id: run ID (= Step Functions execution name); validated first.
        profile: named AWS CLI profile.
        output: local ``Path`` folder for ``progress.json`` and evidence copies.

    Returns:
        The report dict (also printed as JSON).

    Raises:
        ExecutionDoesNotExist: neither the batch nor the cloud execution exists.
        ValueError: an evidence object is larger than 1 MB (unexpected).
        botocore ClientError: on any other AWS error (e.g. missing evidence).
    """
    valid_run(run_id)
    session = boto3.Session(profile_name=profile, region_name=REGION)
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3}, connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    # One client per service, all sharing the retry/timeout config above.
    states, glue, sm, s3 = [session.client(name, config=config) for name in ['stepfunctions', 'glue', 'sagemaker', 's3']]
    # The run ID is used as the execution name on both state machines, so the
    # ARNs can be built directly instead of searched for.
    arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:{run_id}'
    cloud_arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-cloud-ingest:{run_id}'
    try:
        cloud = states.describe_execution(executionArn=cloud_arn)
    except states.exceptions.ExecutionDoesNotExist:
        cloud = None
    # Prefer the batch execution. If it never started (e.g. cloud acquisition
    # failed before handing over), fall back to reporting the cloud one.
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
    # Walk the full execution history (paginated) to list every state entered
    # in order, and to harvest Athena query IDs from successful Athena tasks.
    report['states_entered'] = []
    query_ids = set()
    for page in states.get_paginator('get_execution_history').paginate(executionArn=arn, includeExecutionData=True):
        for event in page['events']:
            if 'stateEnteredEventDetails' in event:
                report['states_entered'].append({'name': event['stateEnteredEventDetails']['name'], 'time': event['timestamp']})
            details = event.get('taskSucceededEventDetails', {})
            if details.get('resourceType') == 'athena' and details.get('output'):
                # Athena task output is either a full GetQueryExecution response
                # (startQueryExecution.sync) or just the StartQueryExecution result.
                result = json.loads(details['output'])
                result = result.get('QueryExecution', result)
                if result.get('QueryExecutionId'): query_ids.add(result['QueryExecutionId'])
    # Fetch status, data-scanned statistics and result location of each query.
    report['athena'] = []
    athena = session.client('athena', config=config)
    for query_id in sorted(query_ids):
        query = athena.get_query_execution(QueryExecutionId=query_id)['QueryExecution']
        report['athena'].append({key: query[key] for key in
            ['QueryExecutionId', 'Status', 'Statistics', 'ResultConfiguration', 'WorkGroup'] if key in query})
    p = paths(run_id)
    # Glue job runs carry no run ID tag, so match them by their output-path
    # argument: curate writes to .../curated/ (--output) and score to
    # .../scored/ (--OUTPUT). Only runs belonging to this run ID are kept.
    report['glue'] = []
    for name, argument, directory in [('curate', '--output', 'curated'), ('score', '--OUTPUT', 'scored')]:
        for page in glue.get_paginator('get_job_runs').paginate(JobName='is459-g3-ingest-' + name):
            for run in page['JobRuns']:
                if run.get('Arguments', {}).get(argument) == f"s3://{BUCKET}/{p[directory]}/":
                    report['glue'].append({key: run[key] for key in ['JobName', 'Id', 'JobRunState', 'ExecutionTime', 'StartedOn', 'CompletedOn', 'ErrorMessage'] if key in run})
    # SageMaker job names are deterministic: is459-g3-<stage>-<run_id>.
    # 'train' only exists for --train runs, so a "not found" error is skipped.
    report['sagemaker'] = []
    for kind, stage in [('Processing', 'baseline'), ('Processing', 'export'), ('Training', 'train')]:
        name = f'is459-g3-{stage}-{run_id}'
        try:
            job = sm.describe_processing_job(ProcessingJobName=name) if kind == 'Processing' else sm.describe_training_job(TrainingJobName=name)
        except ClientError as error:
            if error.response['Error']['Code'] == 'ValidationException' and any(text in str(error) for text in ['not find', 'not found', 'does not exist']):
                continue
            raise
        # Keep timing and billable seconds so run cost can be reported.
        fields = [kind+'JobName', kind+'JobStatus', kind+'StartTime', kind+'EndTime', 'FailureReason', 'TrainingTimeInSeconds', 'BillableTimeInSeconds', 'ModelArtifacts']
        report['sagemaker'].append({key: job[key] for key in fields if key in job})
    if 'output' in execution: report['result'] = json.loads(execution['output'])
    output.mkdir(parents=True, exist_ok=True)
    (output / 'progress.json').write_text(json.dumps(report, indent=2, default=str))
    # For a real (non-cached) successful run, copy the small evidence files:
    # baseline proof, BQ1 score evidence, BQ2 corridor evidence, the dashboard
    # release manifest and the final result. A cache hit reused an older run's
    # outputs, so this run ID has no evidence of its own.
    if execution['status'] == 'SUCCEEDED' and not report.get('result', {}).get('cache_hit'):
        for suffix in ['baseline/proof.json', 'export/evidence/scores.json', 'export/evidence/corridors.json', 'export/release/manifest.json', 'evidence/result.json']:
            response = s3.get_object(Bucket=BUCKET, Key=f'pipeline/runs/{run_id}/{suffix}')
            # Read at most 1 MB + 1 byte: enough to detect an unexpectedly large file
            # without downloading all of it.
            with response['Body'] as stream: data = stream.read(1_000_001)
            if len(data) > 1_000_000: raise ValueError('Unexpected evidence size')
            (output / suffix.replace('/', '-')).write_bytes(data)
    print(json.dumps(report, indent=2, default=str))
    return report


# CLI entry point: --run-id and --output are required.
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inspect(args.run_id, args.profile, args.output)
