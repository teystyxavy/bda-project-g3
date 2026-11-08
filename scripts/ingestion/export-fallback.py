"""Prepare one isolated export fallback, then watch the original without stopping it.

The watcher starts at most one paid run, only after FAILED/TIMEOUT and only when
the shared export destination is empty and no export worker owns it. It never
publishes, changes the shared job/workflow, deletes outputs, or retries a launch
whose outcome is uncertain. Keep the local computer awake while watching.
"""
import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[2]
BUCKET = 'is459-g3-588738581042-ap-southeast-1'
REGION = 'ap-southeast-1'
PATCHED = ('network.py', 'network_checkpoint.py', 'ingest_compute.py', 'glue_compute.py')


def write_state(directory, status, **details):
    value = {'status': status, 'updated_at': datetime.now(timezone.utc).isoformat(), **details}
    target = directory / 'status.json'
    temporary = directory / 'status.tmp'
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(target)
    print(json.dumps(value), flush=True)


def clients(profile):
    session = boto3.Session(profile_name=profile, region_name=REGION)
    # StartJobRun has no idempotency token: never transparently retry launches.
    config = Config(retries={'total_max_attempts': 1}, connect_timeout=10, read_timeout=60)
    return session.client('glue', config=config), session.client('s3', config=config)


def build_package(directory):
    destination = directory / 'bda_pipeline.zip'
    with zipfile.ZipFile(directory / 'original.zip') as original, zipfile.ZipFile(
            destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        names = set(original.namelist()) | {'bda_pipeline/' + name for name in PATCHED}
        for name in sorted(names):
            data = ((ROOT / 'src' / name).read_bytes() if name in {'bda_pipeline/' + n for n in PATCHED}
                    else original.read(name))
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return destination


def prepare(glue, s3, directory, original_job, original_run, recovery_id=None):
    if (directory / 'plan.json').exists():
        raise ValueError('Fallback already prepared; use its existing plan')
    if recovery_id and not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', recovery_id):
        raise ValueError('Unexpected recovery identity')
    run = glue.get_job_run(JobName=original_job, RunId=original_run)['JobRun']
    job = glue.get_job(JobName=original_job)['Job']
    if run['Arguments'].get('--STAGE') != 'export':
        raise ValueError('Only export recovery is supported')
    package = build_package(directory)
    script = ROOT / 'jobs/pipeline_stage.py'
    version = hashlib.sha256(package.read_bytes() + script.read_bytes()).hexdigest()
    prefix = 'pipeline/deployments/export-fallback-' + version
    name = 'is459-g3-ingest-export-fallback-' + version[:12]
    for path, filename in ((package, 'bda_pipeline.zip'), (script, 'pipeline_stage.py')):
        s3.upload_file(str(path), BUCKET, prefix + '/' + filename,
                       ExtraArgs={'ServerSideEncryption': 'AES256'})
    pinned = dict(job['NonOverridableArguments'])
    pinned.update({'--extra-py-files': f's3://{BUCKET}/{prefix}/bda_pipeline.zip',
                   '--custom-logGroup-prefix': '/aws-glue/is459-g3-ingest-export-fallback',
                   '--NETWORK_CHECKPOINTS': 'true'})
    create = {'Name': name, 'Role': job['Role'], 'ExecutionProperty': {'MaxConcurrentRuns': 1},
              'Command': dict(job['Command'], ScriptLocation=f's3://{BUCKET}/{prefix}/pipeline_stage.py'),
              'NonOverridableArguments': pinned, 'MaxRetries': 0, 'Timeout': run['Timeout'],
              'WorkerType': run['WorkerType'], 'NumberOfWorkers': run['NumberOfWorkers'],
              'GlueVersion': job['GlueVersion'], 'Description': 'One-shot fallback; original export remains untouched'}
    glue.create_job(**create)
    arguments = dict(run['Arguments'])
    if recovery_id:
        arguments.update({'--RECOVERY_ID': recovery_id,
            '--OUTPUT': f"s3://{BUCKET}/tmp/ingest/recovery-exports/{arguments['--RUN_ID']}/{recovery_id}/"})
    plan = {'original_job': original_job, 'original_run': original_run, 'fallback_job': name,
            'arguments': arguments, 'package_sha256': hashlib.sha256(package.read_bytes()).hexdigest(),
            'artifact_prefix': prefix, 'worker_type': run['WorkerType'], 'workers': run['NumberOfWorkers'],
            'timeout_minutes': run['Timeout']}
    (directory / 'plan.json').write_text(json.dumps(plan, indent=2))
    (directory / 'arguments.json').write_text(json.dumps(arguments, indent=2))
    write_state(directory, 'prepared', fallback_job=name, original_run=original_run)


def safe_to_launch(glue, s3, plan, original):
    if original['JobRunState'] not in ('FAILED', 'TIMEOUT'):
        raise ValueError('Original has not failed or timed out')
    if original['Arguments'] != plan['arguments']:
        raise ValueError('Original run arguments changed')
    uri = plan['arguments']['--OUTPUT']
    if not uri.startswith(f's3://{BUCKET}/pipeline/runs/') or not uri.endswith('/export/'):
        raise ValueError('Unexpected export destination')
    prefix = uri[len(f's3://{BUCKET}/'):]
    if s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1).get('KeyCount', 0):
        raise ValueError('Export destination contains files; refusing to overwrite')
    active = {'STARTING', 'RUNNING', 'STOPPING', 'WAITING'}
    for page in glue.get_paginator('get_jobs').paginate():
        for job in page['Jobs']:
            for runs in glue.get_paginator('get_job_runs').paginate(JobName=job['Name']):
                for run in runs['JobRuns']:
                    if run['JobRunState'] in active and run.get('Arguments', {}).get('--OUTPUT') == uri:
                        raise ValueError('An active worker still owns this export destination')


def watch(glue, s3, directory):
    plan = json.loads((directory / 'plan.json').read_text())
    lock = directory / 'watch.lock'
    # An exclusive file prevents two watcher processes from launching twice.
    with lock.open('x') as stream:
        stream.write(datetime.now(timezone.utc).isoformat())
    try:
        while True:
            original = glue.get_job_run(JobName=plan['original_job'], RunId=plan['original_run'])['JobRun']
            state = original['JobRunState']
            if state == 'SUCCEEDED':
                write_state(directory, 'original_succeeded_no_fallback')
                return
            if state in ('STOPPED', 'ERROR', 'EXPIRED'):
                write_state(directory, 'original_terminal_no_fallback', original_status=state)
                return
            if state in ('FAILED', 'TIMEOUT'):
                safe_to_launch(glue, s3, plan, original)
                write_state(directory, 'launching_once', fallback_job=plan['fallback_job'])
                # A crash after this boundary must be inspected, never relaunched.
                result = glue.start_job_run(JobName=plan['fallback_job'], Arguments=plan['arguments'])
                write_state(directory, 'fallback_started', fallback_job=plan['fallback_job'],
                            fallback_run=result['JobRunId'], original_status=state)
                return
            write_state(directory, 'watching_original', original_status=state, fallback_job=plan['fallback_job'])
            time.sleep(60)
    except Exception as error:
        write_state(directory, 'watcher_error_inspect_before_retry', error=str(error))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'watch'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--profile', default='is459-g3')
    parser.add_argument('--original-job', default='is459-g3-ingest-export')
    parser.add_argument('--original-run')
    parser.add_argument('--recovery-id', help='Use isolated scratch output for a concurrent export')
    args = parser.parse_args()
    glue, s3 = clients(args.profile)
    if args.action == 'prepare':
        if not args.original_run:
            parser.error('--original-run is required for prepare')
        prepare(glue, s3, args.directory.resolve(), args.original_job, args.original_run, args.recovery_id)
    else:
        watch(glue, s3, args.directory.resolve())


if __name__ == '__main__':
    main()
