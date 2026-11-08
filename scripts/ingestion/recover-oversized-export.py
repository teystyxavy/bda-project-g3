"""Prepare and launch one checkpoint-preserving isolated component-review export.

Wait for the existing fallback to finish; launch only on the exact oversized
ring failure with a complete matching checkpoint and an empty destination.
The explicit hardened handover stops only the corrected consumer, and only
after the producer commits its complete result. Never publish a release or
retry an ambiguous paid launch.
"""
import argparse
import hashlib
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[2]
BUCKET = 'is459-g3-588738581042-ap-southeast-1'
JOB = 'is459-g3-ingest-export-fallback-a8b3b9b43adf'
RUN = 'jr_fcc2ed54c482188a96ecbd9fa029c43c5c9c464c0667cbcb217c7f6380c0cdff'
ACTIVE = {'STARTING', 'RUNNING', 'STOPPING', 'WAITING'}


def read_json(s3, key):
    with s3.get_object(Bucket=BUCKET, Key=key)['Body'] as body:
        return json.loads(body.read())


def state(directory, status, **details):
    value = dict(status=status, checked_at=datetime.now(timezone.utc).isoformat(), **details)
    temp = directory / 'status.tmp'
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(directory / 'status.json')
    print(json.dumps(value), flush=True)


def prepare(glue, s3, directory, hardened=False):
    directory.mkdir(parents=True, exist_ok=False)
    job = glue.get_job(JobName=JOB)['Job']
    run = glue.get_job_run(JobName=JOB, RunId=RUN)['JobRun']
    original = ROOT / 'output/export-concurrent-20261108/bda_pipeline.zip'
    package = directory / 'bda_pipeline.zip'
    # Overlay tested modules; the exact original network hash remains an
    # explicit compatibility identity for migration of completed checkpoints.
    replaced = {'bda_pipeline/' + name for name in
                ('ingest_compute.py', 'glue_compute.py', 'network_review.py', 'network_checkpoint.py',
                 'network.py', 'dashboard_exploration.py', 'export_checkpoint.py', 'cloud_scores.py')}
    base_plan = json.loads((ROOT / 'output/export-concurrent-20261108/plan.json').read_text())
    if hashlib.sha256(original.read_bytes()).hexdigest() != base_plan['package_sha256']:
        raise ValueError('Original fallback package checksum differs')
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as target:
        for name in sorted(set(source.namelist()) | replaced):
            data = (ROOT / 'src' / name).read_bytes() if name in replaced else source.read(name)
            target.writestr(name, data)
        network_hash = hashlib.sha256(source.read('bda_pipeline/network.py')).hexdigest()
    # Pin the previous package's full bytes, not a possibly edited local module.
    source_uri = f"s3://{BUCKET}/{base_plan['artifact_prefix']}/bda_pipeline.zip"
    remote = directory / 'source.zip'
    s3.download_file(BUCKET, source_uri.split(BUCKET + '/', 1)[1], str(remote))
    if remote.read_bytes() != original.read_bytes():
        raise ValueError('Active package differs from reviewed source package')
    script = ROOT / 'jobs/pipeline_stage.py'
    # Use the deployed JS as the base; inject only the reviewed disclosure.
    js_uri = next(uri for uri in job['NonOverridableArguments']['--extra-files'].split(',')
                  if uri.endswith('/dashboard.js'))
    js = directory / 'dashboard.js'
    s3.download_file(BUCKET, js_uri.split(BUCKET + '/', 1)[1], str(js))
    text = js.read_text(encoding='utf-8')
    anchors = [line for line in text.splitlines() if
               "if(snapshot.pipeline)" in line and "element('network-context').innerHTML" in line]
    if len(anchors) != 1:
        raise ValueError('Require one deployed network-context disclosure anchor')
    disclosure = """  if(data.component_review?.components) {
    const review=data.component_review;
    element('network-context').appendChild(document.createTextNode(` · ${integer(review.components)} oversized connected groups (${integer(review.accounts)} accounts) withheld from investigation rings; separate network review required. Full-population totals retained.`));
  }"""
    js.write_text(text if 'const review=data.component_review;' in text
                  else text.replace(anchors[0], anchors[0] + '\n' + disclosure), encoding='utf-8')
    version = hashlib.sha256(package.read_bytes() + script.read_bytes() + js.read_bytes()).hexdigest()
    parent = source_uri.rsplit('/', 1)[0].split(BUCKET + '/', 1)[1]
    prefix = parent + '/component-review-' + version
    for path in (package, script, js):
        s3.upload_file(str(path), BUCKET, prefix + '/' + path.name,
                       ExtraArgs={'ServerSideEncryption': 'AES256'})
    pinned = dict(job['NonOverridableArguments'])
    pinned['--extra-py-files'] = f's3://{BUCKET}/{prefix}/bda_pipeline.zip'
    pinned['--extra-files'] = pinned['--extra-files'].replace(js_uri, f's3://{BUCKET}/{prefix}/dashboard.js')
    pinned['--OVERSIZED_COMPONENT_REVIEW'] = 'true'
    pinned['--NETWORK_COMPATIBLE_CODE'] = network_hash
    if hardened:
        pinned['--NETWORK_COMPATIBLE_CODE'] = network_hash
        pinned['--CHECKPOINT_READ_ONLY'] = 'false'
    fields = ('Description', 'Role', 'ExecutionProperty', 'DefaultArguments', 'Connections',
              'MaxRetries', 'Timeout', 'WorkerType', 'NumberOfWorkers', 'SecurityConfiguration',
              'NotificationProperty', 'GlueVersion', 'ExecutionClass', 'JobRunQueuingEnabled',
              'MaintenanceWindow')
    update = {key: job[key] for key in fields if key in job}
    update.update(Command=dict(job['Command'], ScriptLocation=f's3://{BUCKET}/{prefix}/pipeline_stage.py'),
                  NonOverridableArguments=pinned)
    if hardened:
        update['Timeout'] = 720
        update['ExecutionProperty'] = {'MaxConcurrentRuns': 2}
    plan = dict(job=JOB, wait_run=RUN, arguments=run['Arguments'], update=update,
                network_code_sha256=network_hash, artifact_prefix=prefix,
                package_sha256=hashlib.sha256(package.read_bytes()).hexdigest())
    if hardened:
        plan['replace_run'] = 'jr_a97783e3fc816f109c66a55ad918e16a837232a65cb9fc328c6ec7816a591ea1'
        plan['arguments'] = dict(plan['arguments'], **{'--RECOVERY_ATTEMPT': 'hardened-20261108'})
        plan['arguments']['--OUTPUT'] += 'hardened-20261108/'
    (directory / 'plan.json').write_text(json.dumps(plan, indent=2))
    state(directory, 'prepared', artifact_prefix=prefix)


def ready(glue, s3, plan, run):
    if run['JobRunState'] != 'FAILED' or 'Implausible network: one ring has' not in run.get('ErrorMessage', ''):
        raise ValueError('Require the specific oversized-component failure')
    if run['Arguments'] != plan['arguments']:
        raise ValueError('Source run arguments differ')
    prefix = plan['arguments']['--OUTPUT'].split(BUCKET + '/', 1)[1]
    if s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1).get('KeyCount', 0):
        raise ValueError('Destination is not empty; inspect before recovery')
    for page in glue.get_paginator('get_job_runs').paginate(JobName=plan['job']):
        if any(item['JobRunState'] in ACTIVE for item in page['JobRuns']):
            raise ValueError('Existing fallback worker still active')
    run_id = plan['arguments']['--RUN_ID']
    raw_key = f'pipeline/runs/{run_id}/raw/manifest.json'
    with s3.get_object(Bucket=BUCKET, Key=raw_key)['Body'] as body:
        manifest_hash = hashlib.sha256(body.read()).hexdigest()
    matches = []
    for page in s3.get_paginator('list_objects_v2').paginate(
            Bucket=BUCKET, Prefix=f'tmp/ingest/export-checkpoints/{run_id}/'):
        for item in page.get('Contents', []):
            if item['Key'].endswith('/result/manifest.json'):
                checkpoint = read_json(s3, item['Key'])
                identity = checkpoint['identity']
                if (identity['network_code_sha256'] == plan['network_code_sha256']
                        and identity['manifest_sha256'] == manifest_hash):
                    matches.append(item['Key'])
    if len(matches) != 1:
        raise ValueError('Require exactly one complete matching network checkpoint')
    return matches[0]


def watch(glue, s3, directory):
    plan = json.loads((directory / 'plan.json').read_text())
    with (directory / 'watch.lock').open('x') as stream:
        stream.write(datetime.now(timezone.utc).isoformat())
    try:
        while True:
            run = glue.get_job_run(JobName=plan['job'], RunId=plan['wait_run'])['JobRun']
            if run['JobRunState'] in ACTIVE:
                state(directory, 'waiting_for_network_checkpoint', source_status=run['JobRunState'])
                time.sleep(45)
                continue
            if run['JobRunState'] == 'SUCCEEDED':
                state(directory, 'source_succeeded_no_recovery')
                return
            checkpoint = ready(glue, s3, plan, run)
            # Only this isolated job's future definition changes, after its worker stops.
            glue.update_job(JobName=plan['job'], JobUpdate=plan['update'])
            state(directory, 'launching_once', checkpoint=checkpoint)
            response = glue.start_job_run(JobName=plan['job'], Arguments=plan['arguments'])
            state(directory, 'corrected_export_started', run_id=response['JobRunId'], checkpoint=checkpoint)
            return
    except Exception as error:
        state(directory, 'inspect_before_retry', error=str(error))
        raise


def start_now(glue, s3, directory):
    """Explicit concurrent launch; own output and read-only checkpoint access."""
    plan = json.loads((directory / 'plan.json').read_text())
    with (directory / 'launch.lock').open('x') as stream:
        stream.write(datetime.now(timezone.utc).isoformat())
    arguments = dict(plan['arguments'])
    arguments['--RECOVERY_ATTEMPT'] = 'backup-now-20261108'
    arguments['--OUTPUT'] += arguments['--RECOVERY_ATTEMPT'] + '/'
    prefix = arguments['--OUTPUT'].split(BUCKET + '/', 1)[1]
    if s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1).get('KeyCount', 0):
        raise ValueError('Concurrent destination already contains files')
    for page in glue.get_paginator('get_job_runs').paginate(JobName=plan['job']):
        if any(item['JobRunState'] in ACTIVE and item.get('Arguments', {}).get('--OUTPUT') == arguments['--OUTPUT']
               for item in page['JobRuns']):
            raise ValueError('Concurrent destination already has an active owner')
    # Existing exact job/role and allowed scratch subtree; no IAM change.
    update = dict(plan['update'])
    update['ExecutionProperty'] = {'MaxConcurrentRuns': 2}
    update['NonOverridableArguments'] = dict(update['NonOverridableArguments'], **{'--CHECKPOINT_READ_ONLY': 'true'})
    glue.update_job(JobName=plan['job'], JobUpdate=update)
    state(directory, 'launching_once', output=arguments['--OUTPUT'])
    response = glue.start_job_run(JobName=plan['job'], Arguments=arguments)
    state(directory, 'concurrent_corrected_export_started', run_id=response['JobRunId'],
          output=arguments['--OUTPUT'], checkpoint_mode='read-only; latest complete checkpoint at load time')


def complete_source_checkpoint(s3, plan):
    """Require the source's complete, matching result commit before any stop."""
    run_id = plan['arguments']['--RUN_ID']
    with s3.get_object(Bucket=BUCKET, Key=f'pipeline/runs/{run_id}/raw/manifest.json')['Body'] as stream:
        manifest_hash = hashlib.sha256(stream.read()).hexdigest()
    expected = {'totals', 'candidates', 'pairs', 'loops', 'ring_members', 'ring_edges', 'accounts', 'ring_payments'}
    matches = []
    for page in s3.get_paginator('list_objects_v2').paginate(
            Bucket=BUCKET, Prefix=f'tmp/ingest/export-checkpoints/{run_id}/'):
        for item in page.get('Contents', []):
            if not item['Key'].endswith('/result/manifest.json'):
                continue
            manifest = read_json(s3, item['Key'])
            identity = manifest['identity']
            if (identity.get('network_code_sha256') != plan['network_code_sha256']
                    or identity.get('manifest_sha256') != manifest_hash):
                continue
            if set(manifest['files']) != expected:
                raise ValueError('Incomplete source result checkpoint table set')
            parent = item['Key'].removesuffix('manifest.json')
            for name, checksum in manifest['files'].items():
                if not re.fullmatch('[0-9a-f]{64}', checksum):
                    raise ValueError('Invalid checkpoint checksum')
                if s3.head_object(Bucket=BUCKET, Key=parent + name + '.parquet')['ContentLength'] <= 0:
                    raise ValueError('Incomplete source result checkpoint file')
            matches.append(item['Key'])
    if len(matches) > 1:
        raise ValueError('Ambiguous matching source checkpoints')
    return matches[0] if matches else None


def corrected_is_near_network_completion(logs, run_id):
    """Avoid throwing away work when a restart would likely be slower.

    This is scheduling only. It never substitutes log text for verification.
    A stopped/failed consumer can still recover from the producer checkpoint.
    """
    response = logs.get_log_events(logGroupName='/aws-glue/is459-g3-ingest-export-fallback/output',
                                   logStreamName=run_id, limit=250)
    messages = '\n'.join(item['message'] for item in response.get('events', []))
    if any(marker in messages for marker in (
            'EXPORT_PHASE network_recomputed', 'EXPORT_PHASE model_view', 'EXPORT_PHASE exploration',
            'STAGE_COMPUTE_COMPLETE', 'phase=payment_pass_complete', 'phase=payment_times_start',
            'phase=timing_checks_start', 'restored=result', 'phase=result_restored')):
        return True
    progress = re.findall(r'phase=payment_pass rows=(\d+) total=(\d+)', messages)
    return any(int(total) > 0 and int(done) / int(total) >= .75 for done, total in progress)


def watch_hardened(glue, s3, directory, logs=None):
    plan = json.loads((directory / 'plan.json').read_text())
    with (directory / 'watch.lock').open('x') as stream:
        stream.write(datetime.now(timezone.utc).isoformat())
    try:
        keep_current = False
        while True:
            current = glue.get_job_run(JobName=plan['job'], RunId=plan['replace_run'])['JobRun']
            if current['JobRunState'] == 'SUCCEEDED':
                state(directory, 'corrected_succeeded_no_replacement')
                return
            checkpoint = complete_source_checkpoint(s3, plan)
            if checkpoint:
                if (current['JobRunState'] in ACTIVE and (keep_current or
                        (logs is not None and corrected_is_near_network_completion(logs, plan['replace_run'])))):
                    keep_current = True
                    state(directory, 'corrected_near_completion_keep_running', checkpoint=checkpoint,
                          corrected_status=current['JobRunState'])
                    time.sleep(45)
                    continue
                break
            source = glue.get_job_run(JobName=plan['job'], RunId=plan['wait_run'])['JobRun']
            if source['JobRunState'] not in ACTIVE:
                raise ValueError('Source finished without a complete network checkpoint; inspect before recovery')
            state(directory, 'waiting_for_complete_network_checkpoint', corrected_status=current['JobRunState'])
            time.sleep(45)
        prefix = plan['arguments']['--OUTPUT'].split(BUCKET + '/', 1)[1]
        if s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1).get('KeyCount', 0):
            raise ValueError('Hardened output destination already contains files')
        # Only stop the explicitly identified corrected run. The checkpoint
        # producer is never stopped, changed, or rolled back.
        if current['JobRunState'] in ACTIVE:
            state(directory, 'stopping_corrected_after_checkpoint', checkpoint=checkpoint,
                  replace_run=plan['replace_run'])
            response = glue.batch_stop_job_run(JobName=plan['job'], JobRunIds=[plan['replace_run']])
            if response.get('Errors'):
                raise ValueError('Corrected run stop rejected: ' + str(response['Errors']))
            deadline = time.monotonic() + 1800
            while current['JobRunState'] in ACTIVE:
                if time.monotonic() >= deadline:
                    raise ValueError('Corrected worker did not stop; no replacement launched')
                time.sleep(15)
                current = glue.get_job_run(JobName=plan['job'], RunId=plan['replace_run'])['JobRun']
            if current['JobRunState'] == 'SUCCEEDED':
                state(directory, 'corrected_succeeded_during_stop_no_replacement')
                return
        for page in glue.get_paginator('get_job_runs').paginate(JobName=plan['job']):
            if any(item['JobRunState'] in ACTIVE
                   and item.get('Arguments', {}).get('--OUTPUT') == plan['arguments']['--OUTPUT']
                   for item in page['JobRuns']):
                raise ValueError('Another worker already owns hardened destination')
        glue.update_job(JobName=plan['job'], JobUpdate=plan['update'])
        state(directory, 'launching_once', checkpoint=checkpoint)
        response = glue.start_job_run(JobName=plan['job'], Arguments=plan['arguments'], Timeout=720)
        state(directory, 'hardened_export_started', run_id=response['JobRunId'], checkpoint=checkpoint,
              output=plan['arguments']['--OUTPUT'], timeout_minutes=720)
    except Exception as error:
        state(directory, 'inspect_before_retry', error=str(error))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'prepare-hardened', 'watch', 'start-now', 'watch-hardened'])
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name='ap-southeast-1')
    config = Config(retries={'total_max_attempts': 1}, connect_timeout=10, read_timeout=60)
    glue, s3 = session.client('glue', config=config), session.client('s3', config=config)
    if args.action == 'prepare-hardened':
        prepare(glue, s3, args.directory.resolve(), hardened=True)
    elif args.action == 'watch-hardened':
        watch_hardened(glue, s3, args.directory.resolve(), session.client('logs', config=config))
    else:
        {'prepare': prepare, 'watch': watch, 'start-now': start_now,
         'watch-hardened': watch_hardened}[args.action](glue, s3, args.directory.resolve())


if __name__ == '__main__':
    main()
