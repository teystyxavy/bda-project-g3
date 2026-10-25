"""Read-only timings and stage-cache evidence for a completed ingestion run.

Purpose
-------
After the dashboard-triggered ingestion pipeline has finished a run, this
script collects *how long it took* and *which stages were reused from the
stage cache*, so the team can report performance honestly (e.g. "the second
run was N% faster because curated data was reused").

Where it sits
-------------
The cloud ingestion has two Step Functions state machines, both started
with the same execution name = run ID:

* ``is459-g3-cloud-ingest`` - the outer wrapper (acquisition from Kaggle and
  FX sources, then it starts the inner workflow);
* ``is459-g3-ingest`` - the inner batch workflow (curation, scoring, Athena,
  SageMaker Processing ``Baseline`` and ``Export`` jobs, then ``Publish``).

AWS reads (no writes; IAM needs only Describe/Get/List permissions):

* ``states:DescribeExecution`` for both executions (start/stop timestamps);
* ``states:GetExecutionHistory`` for the inner run, to find when the states
  after the processing jobs (``PrepareBranches`` after Baseline, ``Publish``
  after Export) were entered;
* ``sagemaker:DescribeProcessingJob`` for ``is459-g3-baseline-<run>`` and
  ``is459-g3-export-<run>``;
* ``s3:GetObject`` on ``s3://<BUCKET>/pipeline/runs/<run>/raw/manifest.json``,
  ``.../evidence/stage-cache.json`` and ``.../evidence/result.json``.

Output: a local JSON report at ``--output``; a summary is printed.

Usage::

    python scripts/benchmarks/benchmark-ingestion.py --run-id <run> \\
        --output output/bench.json [--previous older-report.json] \\
        [--profile default]
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

import boto3
from botocore.config import Config

# Shared constants (AWS account, region, private data bucket) and the run-ID
# validator, so this script cannot drift from the deployed naming contract.
from bda_pipeline.common.ingest_contract import ACCOUNT, BUCKET, REGION, valid_run


def benchmark(run_id, profile, output, previous=None):
    """Collect execution timings and evidence for one finished run.

    Args:
        run_id: pipeline run ID (also the Step Functions execution name).
        profile: AWS CLI profile name to use for credentials.
        output: Path of the JSON report to write (parent dirs are created).
        previous: optional Path to an earlier report (or a raw
            DescribeExecution JSON) to compare against.

    Side effects:
        Read-only AWS API calls; writes ``output`` and prints a summary.

    Raises:
        ValueError: if the run ID is unsafe, either execution did not
            succeed, or an evidence object is larger than 100 KB.
    """
    # Validate the run ID first (lowercase/digits/hyphen, 3-36 chars) because
    # it is inserted into ARNs and S3 keys below.
    valid_run(run_id)
    session = boto3.Session(profile_name=profile, region_name=REGION)
    # Standard retry mode with at most 3 attempts and short timeouts: enough to
    # ride out a transient throttle without hanging. user_agent_appid tags the
    # requests so they can be identified in CloudTrail.
    sdk = Config(retries={'mode': 'standard', 'total_max_attempts': 3}, connect_timeout=10,
                 read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    states, s3 = [session.client(name, config=sdk) for name in ['stepfunctions', 's3']]
    executions = {}
    # Timing of the outer wrapper and the inner workflow. Only successful runs
    # are meaningful benchmarks, so anything else stops the script.
    for machine in ['is459-g3-cloud-ingest', 'is459-g3-ingest']:
        arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:{machine}:{run_id}'
        execution = states.describe_execution(executionArn=arn)
        if execution['status'] != 'SUCCEEDED': raise ValueError('Benchmark requires a successful completed run')
        executions[machine] = {'start': execution['startDate'].isoformat(), 'stop': execution['stopDate'].isoformat(),
                               'seconds': (execution['stopDate'] - execution['startDate']).total_seconds()}
    report = {'run_id': run_id, 'executions': executions, 'processing_completion': {}}
    completed = {}
    # Walk the inner workflow's event history. The time a state is *entered*
    # marks when orchestration noticed the previous processing job was done:
    # PrepareBranches follows the Baseline job, Publish follows the Export job.
    for page in states.get_paginator('get_execution_history').paginate(
            executionArn=f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:{run_id}', includeExecutionData=True):
        for event in page['events']:
            entry = event.get('stateEnteredEventDetails', {})
            if entry.get('name') in ['PrepareBranches', 'Publish']:
                completed['baseline' if entry['name'] == 'PrepareBranches' else 'export'] = event['timestamp']
    # Compare each SageMaker Processing job's own start/end time with the
    # moment Step Functions moved on. The gap shows orchestration overhead
    # (output upload, status propagation, polling) separately from compute.
    sm = session.client('sagemaker', config=sdk)
    for stage, next_time in completed.items():
        job = sm.describe_processing_job(ProcessingJobName=f'is459-g3-{stage}-{run_id}')
        report['processing_completion'][stage] = {
            'compute_seconds': (job['ProcessingEndTime'] - job['ProcessingStartTime']).total_seconds(),
            'compute_end_to_next_stage_seconds': (next_time - job['ProcessingEndTime']).total_seconds(),
            'service_last_modified_to_next_stage_seconds': (next_time - job['LastModifiedTime']).total_seconds(),
            'note': 'Includes final output upload and service status propagation as well as orchestration detection.'}
    # Attach the run's own evidence documents (input manifest, which stages
    # were reused from cache, final result). Reading at most 100,001 bytes and
    # rejecting anything larger prevents a huge/unexpected object from being
    # loaded into memory.
    for name in ['raw/manifest.json', 'evidence/stage-cache.json', 'evidence/result.json']:
        response = s3.get_object(Bucket=BUCKET, Key=f'pipeline/runs/{run_id}/{name}')
        with response['Body'] as body: data = body.read(100_001)
        if len(data) > 100_000: raise ValueError('Oversized evidence')
        value = json.loads(data)
        report[name] = value
    # Optional before/after comparison. A previous report produced by this
    # script has 'executions'; otherwise the file is assumed to be a raw
    # DescribeExecution response with startDate/stopDate fields.
    if previous:
        reference = json.loads(previous.read_text())
        if 'executions' in reference:
            old_seconds = reference['executions']['is459-g3-cloud-ingest']['seconds']
        else:
            old_seconds = (datetime.fromisoformat(reference['stopDate']) - datetime.fromisoformat(reference['startDate'])).total_seconds()
        # Positive seconds_saved / percent_faster means the current run was quicker.
        # Only one run per configuration is compared, so this is not an SLA claim.
        current_seconds = executions['is459-g3-cloud-ingest']['seconds']
        report['comparison'] = {'reference': str(previous), 'reference_seconds': old_seconds,
                                'seconds_saved': old_seconds - current_seconds,
                                'percent_faster': (old_seconds-current_seconds)/old_seconds*100,
                                'scope': 'One observed run per configuration; no percentile or SLA claim.'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2))
    print(json.dumps({key: report[key] for key in ['run_id', 'executions', 'processing_completion', 'comparison'] if key in report}, indent=2))


def main():
    """Command-line entry point: parse arguments and call :func:`benchmark`."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    # Default profile is the project's least-privilege tools profile.
    parser.add_argument('--profile', default='default')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--previous', type=Path)
    args = parser.parse_args()
    benchmark(args.run_id, args.profile, args.output, args.previous)


if __name__ == '__main__': main()
