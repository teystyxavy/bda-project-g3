"""Read-only source manifest and wrapper evidence for cloud ingestion.

Where this sits in the pipeline
-------------------------------
The default ``ingest.py run`` route acquires data inside AWS: the
``is459-g3-cloud-ingest`` Step Functions "wrapper" invokes the
``is459-g3-acquire`` Lambda (container image from ``cloud-ingestion/``), which
downloads the Kaggle HI-Small files, caches them in S3, fetches FX rates,
writes the run's raw inputs and then starts the batch workflow. This script
audits that acquisition step for one run, without changing anything, so the
team can show *where the data came from* and that it was verified.

What it checks / collects
-------------------------
* The wrapper execution's status and full event history (Lambda/task details).
* The live configuration of the ``is459-g3-acquire`` Lambda (memory, timeout,
  ephemeral storage) and the exact container image digest it ran.
* ``pipeline/runs/<run_id>/raw/manifest.json`` (present only if inputs were
  committed) or ``.../evidence/acquisition-failure.json`` (if it failed).
* The S3 source cache ``pipeline/acquisition/versions/<dataset_version>/``:
  its ``record.json`` and, for each cached Kaggle file, that the object's
  SHA-256 metadata matches the record and that it is SSE-S3 encrypted. If a
  copy exists in the local ``dataset/`` folder its hash is compared too.
* The run's ``fx.csv``: size and SHA-256 must match the manifest, and it is
  re-validated with the same ``validate_fx`` rules used during acquisition
  (complete coverage, positive, not stale, no duplicates).

Writes
------
``<output>/acquisition.json`` (full report) and a one-line summary to stdout.

Usage
-----
    python scripts/ingestion/inspect-cloud-acquisition.py \
        --run-id <run_id> --output output/inspect/<run_id>
"""
import argparse
import hashlib
import io
import json
from datetime import date, timedelta
from pathlib import Path

import boto3
import pandas as pd
from botocore.config import Config
from bda_pipeline.ingestion.acquisition import validate_fx
from bda_pipeline.common.ingest_contract import ACCOUNT, BUCKET, REGION, valid_run


def inspect(run_id, output, profile):
    """Audit the cloud acquisition of ``run_id`` and save ``acquisition.json``.

    Args:
        run_id: run ID (= wrapper execution name); validated first.
        output: local ``Path`` folder for the report.
        profile: named AWS CLI profile.

    Returns:
        None. Writes the report file and prints a short status summary.

    Raises:
        ValueError: an evidence object is unexpectedly large, or the source
            cache lists a file other than the two HI-Small CSVs, or the FX
            file fails validation.
        AssertionError: hash/encryption/size checks on cached sources or fx.csv
            do not match (evidence of tampering or a broken upload).
    """
    valid_run(run_id)
    session = boto3.Session(profile_name=profile, region_name=REGION)
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3},
                    connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    # 'function' is the Lambda client (named to avoid the keyword 'lambda').
    states, s3, function = [session.client(name, config=config) for name in ['stepfunctions', 's3', 'lambda']]
    arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-cloud-ingest:{run_id}'
    execution = states.describe_execution(executionArn=arn)
    report = {key: execution[key] for key in ['executionArn', 'status', 'startDate', 'stopDate', 'error', 'cause'] if key in execution}
    # Keep only the event fields that explain what happened (state names,
    # Lambda results/failures/timeouts, task results) from the full history.
    report['events'] = []
    for page in states.get_paginator('get_execution_history').paginate(executionArn=arn, includeExecutionData=True):
        for event in page['events']:
            item = {'type': event['type'], 'timestamp': event['timestamp']}
            for key in ['stateEnteredEventDetails', 'lambdaFunctionSucceededEventDetails',
                        'lambdaFunctionFailedEventDetails', 'lambdaFunctionTimedOutEventDetails',
                        'taskSucceededEventDetails', 'taskFailedEventDetails']:
                if key in event: item[key] = event[key]
            report['events'].append(item)
    # Record the Lambda's current settings and the resolved image digest, so
    # the report pins exactly which acquisition code version was deployed.
    live = function.get_function(FunctionName='is459-g3-acquire')
    report['function'] = {key: live['Configuration'][key] for key in
                          ['State', 'LastUpdateStatus', 'MemorySize', 'Timeout', 'EphemeralStorage', 'LastModified']}
    report['image_uri'] = live['Code']['ResolvedImageUri']
    # Either a committed manifest (success) or a failure record may exist.
    # List first so a missing key is skipped rather than raising AccessDenied.
    for name, key in [('raw_manifest', f'pipeline/runs/{run_id}/raw/manifest.json'),
                      ('acquisition_failure', f'pipeline/runs/{run_id}/evidence/acquisition-failure.json')]:
        listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
        if not any(item['Key'] == key for item in listing.get('Contents', [])): continue
        response = s3.get_object(Bucket=BUCKET, Key=key)
        # Bounded read: these JSON files are tiny; >100 KB means something is wrong.
        with response['Body'] as body: content = body.read(100_001)
        if len(content) > 100_000: raise ValueError('Unexpected acquisition evidence size')
        report[name] = json.loads(content)
    # Only if inputs were committed: verify the S3 source cache and the FX file.
    if 'raw_manifest' in report:
        version = report['raw_manifest']['dataset_version']
        prefix = f'pipeline/acquisition/versions/{version}'
        response = s3.get_object(Bucket=BUCKET, Key=f'{prefix}/record.json')
        with response['Body'] as body: content = body.read(100_001)
        if len(content) > 100_000: raise ValueError('Unexpected source cache record size')
        report['source_cache'] = json.loads(content)
        # Each cached Kaggle file must carry the recorded SHA-256 in its metadata
        # and be encrypted at rest with SSE-S3 (AES256).
        report['source_objects'] = {}
        for name, digest in report['source_cache']['files'].items():
            if name not in ['HI-Small_Trans.csv', 'HI-Small_accounts.csv']:
                raise ValueError('Unexpected cached source file')
            head = s3.head_object(Bucket=BUCKET, Key=f'{prefix}/{name}')
            assert head['Metadata']['sha256'] == digest
            assert head['ServerSideEncryption'] == 'AES256'
            # Optional cross-check with a local copy (relative to the current dir).
            local = Path('dataset') / name
            if local.is_file():
                with local.open('rb') as stream: local_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            else: local_digest = None
            report['source_objects'][name] = {'bytes': head['ContentLength'], 'sha256': digest,
                'encrypted': True, 'matches_retained_local_source': local_digest == digest if local_digest else None}
        # Re-verify fx.csv byte-for-byte against the manifest, then re-run the
        # FX validation rules over the same window used in acquisition (the day
        # before start_date through end_date, for the causal previous-day rate).
        manifest = report['raw_manifest']
        response = s3.get_object(Bucket=BUCKET, Key=f'pipeline/runs/{run_id}/raw/fx.csv')
        with response['Body'] as body: rates_bytes = body.read(1_000_001)
        assert len(rates_bytes) == manifest['files']['fx.csv']['bytes'] < 1_000_000
        assert hashlib.sha256(rates_bytes).hexdigest() == manifest['files']['fx.csv']['sha256']
        rates = pd.read_csv(io.BytesIO(rates_bytes))
        validate_fx(rates, date.fromisoformat(manifest['start_date']) - timedelta(days=1),
                    date.fromisoformat(manifest['end_date']), manifest['currencies'])
        # Summarise which FX source provided which currencies (provenance).
        report['fx_verified'] = {'status': 'passed', 'rows': len(rates),
            'sources': {source: sorted(group.currency.unique().tolist()) for source, group in rates.groupby('source')},
            'sha256': hashlib.sha256(rates_bytes).hexdigest(),
            'start': str(rates.txn_date.min()), 'end': str(rates.txn_date.max())}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'acquisition.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'input_committed': 'raw_manifest' in report,
                      'failure': report.get('acquisition_failure'),
                      'latest_event': report['events'][-1] if report['events'] else None}, default=str))


# CLI entry point: --run-id and --output are required.
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', default='default')
    args = parser.parse_args()
    inspect(args.run_id, args.output, args.profile)
