"""One-command source-to-dashboard entry point; run starts the configured paid workflow.

Where this sits in the pipeline
-------------------------------
This is the first step of the whole IS459 Group 3 AML pipeline and the script
that ``scripts/ingestion/ingest.ps1`` wraps. It takes the Kaggle IBM
Anti-Money-Laundering "HI-Small" transactions + accounts files, attaches daily
historical FX rates (so every amount can later be converted to USD for BQ2),
uses AWS Lambda acquisition by default, or uploads locally prepared inputs
with ``--local-acquisition``. Step Functions then coordinates Glue curation
and frozen XGBoost scoring, Athena corridor SQL, SageMaker verification and
dashboard publication. Optional SageMaker training creates a private candidate;
it does not replace the frozen model automatically.

Sub-commands
------------
* ``download`` - only download selected Kaggle files into ``--dataset-directory``
  (any of the six subsets). No AWS calls.
* ``prepare`` - download if needed, cut an optional ``--rows`` prefix, fetch FX,
  and write ``transactions.csv``, ``accounts.csv``, ``fx.csv`` and
  ``manifest.json`` to ``output/ingest/<run_id>/``. No upload, no workflow.
  It reads the deployed AWS configuration unless ``--deployment`` supplies
  a local copy; ``--fx-file`` supplies offline rates.
* ``run`` - start the paid workflow. Two routes:
    - default (cloud acquisition): start the ``is459-g3-cloud-ingest`` state
      machine, which downloads from Kaggle and fetches FX inside AWS Lambda;
    - ``--local-acquisition``: do ``prepare`` on this computer, upload to
      ``s3://<BUCKET>/pipeline/runs/<run_id>/raw/`` and start the
      ``is459-g3-ingest`` state machine directly, or (``--trigger eventbridge``)
      write a ``trigger.json`` object that an EventBridge rule reacts to.
  Unless ``--no-wait`` is given, it then polls the execution every 20 seconds.
* ``status --run-id ID`` - print the Step Functions execution state/output.
* ``unlock --run-id ID`` - invoke the ``is459-g3-ingest-control`` Lambda with
  ``op=recover`` to release ``pipeline/lock.json`` after a failed run.

Key S3 objects (bucket ``BUCKET`` from ``bda_pipeline.ingest_contract``)
--------------------------------------------------------------------------
* ``pipeline/deployment.json`` (``CONFIG_KEY``) - deployed configuration; its
  SHA-256 is stored in each manifest so a run is tied to one deployment.
* ``pipeline/runs/<run_id>/raw/{transactions,accounts,fx}.csv`` + ``manifest.json``.
* ``pipeline/runs/<run_id>/raw/upload-claim.json`` - "this run ID is taken" marker.
* ``pipeline/runs/<run_id>/raw/trigger.json`` - EventBridge start signal.
* ``pipeline/cache/<processing_sha256>.json`` - result of an earlier run with
  identical inputs; reused instead of paying for the same work twice.

Example
-------
    python scripts/ingestion/ingest.py --profile is459-g3-tools run --rows 1000
"""
import argparse
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from bda_pipeline.dataset_options import add_selection_arguments, resolve_subset, selected_files, selection_options

from bda_pipeline.ingest_contract import (
    ACCOUNT, BUCKET, REGION, MACHINE, CONFIG_KEY, canonical, sha, paths, valid_run, validate_manifest,
)


def aws_clients(profile):
    """Create authenticated S3 and Step Functions clients for the project account.

    boto3 is imported lazily so ``download``/``prepare --deployment`` (and
    ``--dry-run``) work on a machine without AWS credentials or boto3.

    Args:
        profile: named AWS CLI profile (e.g. ``is459-g3-tools``).

    Returns:
        ``(session, s3_client, stepfunctions_client)``; the session is returned so
        callers can create other clients (``unlock`` uses it for Lambda).

    Raises:
        ValueError: if the credentials belong to an account other than ``ACCOUNT``.

    Side effects:
        Sets the ``AWS_SDK_UA_APP_ID`` environment variable for this process.
    """
    import boto3
    from botocore.config import Config
    os.environ['AWS_SDK_UA_APP_ID'] = 'AWSSkill-SageMaker'
    session = boto3.Session(profile_name=profile, region_name=REGION)
    # Standard retry mode (max 3 attempts) and short timeouts: transient errors
    # are retried, but a broken network fails within about a minute.
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3},
                    connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    # Guard against running (and paying for) the pipeline in the wrong account.
    if session.client('sts', config=config).get_caller_identity()['Account'] != ACCOUNT:
        raise ValueError('Wrong AWS account')
    return session, session.client('s3', config=config), session.client('stepfunctions', config=config)


def read_json(s3, key):
    """Read and parse a JSON object from the project bucket, or return None.

    The key is first checked with ``ListObjectsV2`` instead of relying only on
    ``GetObject``: without ``s3:ListBucket`` permission a missing key returns
    403 AccessDenied rather than 404, so listing gives a clean "not there"
    answer. The ``NoSuchKey`` handler covers the race where the object is
    deleted between the list and the get.

    Args:
        s3: boto3 S3 client.
        key: object key inside ``BUCKET``.

    Returns:
        The decoded JSON value, or None if the object does not exist.
    """
    # Prefix listing with MaxKeys=1: the exact key, if present, sorts first.
    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
    if not any(item['Key'] == key for item in listing.get('Contents', [])): return None
    try:
        response = s3.get_object(Bucket=BUCKET, Key=key)
    except s3.exceptions.NoSuchKey:
        return None
    with response['Body'] as body:
        return json.loads(body.read())


def prepare(args, config):
    """Acquire the dataset locally and build the run's input folder + manifest.

    Steps:
        1. ``acquire_dataset`` downloads (or reuses verified local copies of) the
           HI-Small transactions and accounts CSVs from Kaggle.
        2. A run ID is chosen: ``--run-id`` or ``YYYYmmdd-HHMMSS-<8 hex chars>``
           (UTC time + random suffix, so two runs started in the same second
           still get distinct IDs). ``valid_run`` enforces 3-36 lowercase
           letters/digits/hyphens because the ID is reused in S3 keys, Step
           Functions execution names and Athena table names.
        3. ``prepare_inputs`` copies the first ``--rows`` transactions (or all),
           copies accounts, and builds/validates daily historical FX rates for
           every currency and date in the data, writing three CSVs.
        4. A manifest is written that records what was processed and a hash of it.

    Args:
        args: parsed argparse namespace (``train``, ``rows``, ``dataset_directory``,
            ``dataset_version``, ``refresh``, ``run_id``, ``fx_file``, ``rerun``).
        config: deployed configuration dict (from ``pipeline/deployment.json`` or
            a local ``--deployment`` file).

    Returns:
        ``(manifest_dict, output_path)`` where ``output_path`` is
        ``output/ingest/<run_id>``.

    Raises:
        ValueError: ``--train`` combined with ``--rows`` (training needs the full
            subset so the chronological train/validation/test splits are valid),
            or any validation failure from the helpers.
    """
    from bda_pipeline.acquisition import acquire_dataset, prepare_inputs
    if args.train and args.rows:
        raise ValueError('Training needs the full subset and chronological splits; omit --rows')
    subset = getattr(args, 'subset', 'HI-Small')
    record = acquire_dataset(args.dataset_directory, subset=subset, version=args.dataset_version, refresh=args.refresh)
    run_id = valid_run(args.run_id or datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(4))
    output = Path('output/ingest') / run_id
    info = prepare_inputs(args.dataset_directory / f'{subset}_Trans.csv',
                          args.dataset_directory / f'{subset}_accounts.csv', output,
                          rows=args.rows, fx_file=args.fx_file)
    # "identity" = everything that determines the pipeline's *output*: row count,
    # file hashes, date range, dataset version, deployment hash and training mode.
    # Its SHA-256 (processing_sha256) is the cache key - two runs with the same
    # identity would produce the same results, so the second can reuse the first.
    identity = {**{key: info[key] for key in ['rows', 'files', 'start_date', 'end_date']},
                'subset': subset, 'dataset_version': record['dataset_version'],
                'deployment_sha256': sha(config), 'train': args.train}
    # The manifest adds run-specific metadata (run ID, timestamps, provenance,
    # currencies) that does NOT affect results and so is not part of the hash.
    manifest = {**identity, 'schema_version': 1, 'run_id': run_id,
                'processing_sha256': sha(identity), 'rerun': args.rerun,
                'source_scope': info['scope'], 'currencies': info['currencies'],
                'dataset_origin': record['origin'], 'created_at': datetime.now(timezone.utc).isoformat(),
                'model_manifest_sha256': config['model_manifest_sha256']}
    # Same validator the control Lambda runs in AWS: checks run ID, HI-Small
    # schema, row bounds, the 3-file allowlist, hashes, dates within
    # 2022-09-01..2022-09-18 and that processing_sha256 matches the identity.
    validate_manifest(manifest, sha(config))
    # canonical() = sorted keys, compact separators -> byte-stable JSON.
    (output / 'manifest.json').write_bytes(canonical(manifest))
    print(f"Prepared {info['rows']:,} rows for {run_id}; historical FX {info['start_date']} to {info['end_date']}", flush=True)
    return manifest, output


def await_trigger(run_id, states, timeout=120):
    """Wait for the EventBridge route to start the batch execution for ``run_id``.

    With ``--trigger eventbridge`` this script does not call StartExecution
    itself. Instead the S3 ``Object Created`` event for ``trigger.json`` goes to
    EventBridge, which starts the ``is459-g3-raw-trigger`` wrapper state
    machine, which in turn starts ``is459-g3-ingest`` with the run ID as the
    execution name. This function polls ``DescribeExecution`` on that name.

    Args:
        run_id: validated run ID (also the execution name).
        states: boto3 Step Functions client.
        timeout: seconds to wait before giving up (default 120).

    Returns:
        The execution ARN once the execution exists.

    Raises:
        RuntimeError: if the execution does not appear before the deadline
            (usually the eventbridge-trigger stack is not deployed or disabled).
            The uploaded inputs are left in S3 so the run can be retried.
    """
    arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:{run_id}'
    # monotonic() is immune to wall-clock changes, so the timeout is reliable.
    deadline = time.monotonic() + timeout
    while True:
        try:
            return states.describe_execution(executionArn=arn)['executionArn']
        except states.exceptions.ExecutionDoesNotExist:
            if time.monotonic() > deadline:
                raise RuntimeError('EventBridge did not start the batch; check that infra/eventbridge-trigger is deployed '
                                   'and inspect is459-g3-raw-trigger executions. Uploaded inputs are kept.') from None
            # Not started yet: poll again every 5 seconds.
            time.sleep(5)


def start(manifest, output, s3, states, trigger='direct'):
    """Upload a prepared run to the S3 raw zone and start the batch workflow.

    Order of operations matters:
        1. Cache check (skipped with ``--rerun``): if an earlier run with the same
           ``processing_sha256`` was already published, return its result.
        2. Write ``upload-claim.json`` with ``IfNoneMatch='*'`` (an S3
           conditional write that fails if the object already exists). This
           claims the run ID so two uploads can never mix files under one ID.
        3. Upload the three CSVs with server-side encryption (SSE-S3/AES256) and
           each file's SHA-256 in object metadata; the control Lambda's ``begin``
           step re-checks size and hash before any compute starts.
        4. Write ``manifest.json`` (also create-only).
        5. Start the workflow: either ``StartExecution`` directly, or write
           ``trigger.json`` last and wait for EventBridge to start it.

    Args:
        manifest: validated manifest from :func:`prepare`.
        output: local folder containing the prepared CSVs.
        s3: boto3 S3 client.
        states: boto3 Step Functions client.
        trigger: ``'direct'`` or ``'eventbridge'``.

    Returns:
        A receipt dict (run ID, execution ARN, status ``submitted``, how it was
        started and a console URL), or the cached result with
        ``cache_hit=True``.

    Side effects:
        Uploads to S3, starts a paid Step Functions execution and writes
        ``submission.json`` to the local output folder.
    """
    # Result cache: pipeline/cache/<hash>.json is written by the control Lambda
    # when a run publishes successfully. Only a *published* entry counts.
    if not manifest['rerun']:
        cached = read_json(s3, f"pipeline/cache/{manifest['processing_sha256']}.json")
        if cached and cached.get('status') == 'published':
            return {**cached, 'cache_hit': True}
    # p['raw'] = pipeline/runs/<run_id>/raw (see ingest_contract.paths).
    p = paths(manifest['run_id'])
    # Create-only claim: raises PreconditionFailed if this run ID was used before.
    s3.put_object(Bucket=BUCKET, Key=f"{p['raw']}/upload-claim.json", IfNoneMatch='*',
                  Body=canonical({'run_id': manifest['run_id']}), ServerSideEncryption='AES256')
    for name, info in manifest['files'].items():
        s3.upload_file(str(output / name), BUCKET, f"{p['raw']}/{name}",
                       ExtraArgs={'ServerSideEncryption': 'AES256', 'Metadata': {'sha256': info['sha256']}})
    # The manifest is uploaded after the data files, so its presence implies
    # the data files are already there.
    s3.put_object(Bucket=BUCKET, Key=f"{p['raw']}/manifest.json", Body=canonical(manifest),
                  IfNoneMatch='*', ServerSideEncryption='AES256', ContentType='application/json')
    if trigger == 'eventbridge':
        # Written last: the EventBridge rule matches only this key, so the inputs are complete when it fires.
        s3.put_object(Bucket=BUCKET, Key=f"{p['raw']}/trigger.json", IfNoneMatch='*', ServerSideEncryption='AES256',
                      Body=canonical({'run_id': manifest['run_id'], 'requested_at': datetime.now(timezone.utc).isoformat()}),
                      ContentType='application/json')
        execution_arn = await_trigger(manifest['run_id'], states)
    else:
        # The run ID identifies this execution. Repeating an identical request
        # while a Standard execution is running is idempotent; completed names
        # cannot be immediately reused. The upload claim separately guards data.
        execution_arn = states.start_execution(stateMachineArn=MACHINE, name=manifest['run_id'],
            input=json.dumps({'run_id': manifest['run_id']}))['executionArn']
    # Local receipt so the team can find the execution later in the console.
    receipt = {'run_id': manifest['run_id'], 'execution_arn': execution_arn,
               'status': 'submitted', 'started_by': trigger, 'console_url':
               f"https://{REGION}.console.aws.amazon.com/states/home?region={REGION}#/executions/details/{execution_arn}"}
    (output / 'submission.json').write_bytes(canonical(receipt))
    return receipt


def cloud_start(args, states):
    """Start the fully cloud-side acquisition + batch (default ``run`` route).

    The ``is459-g3-cloud-ingest`` state machine downloads the Kaggle data and
    FX rates inside AWS (Lambda) and then runs the same batch, so nothing is
    uploaded from the laptop. Local-only inputs (``--fx-file``,
    ``--deployment``) are therefore rejected.

    Args:
        args: parsed argparse namespace for ``run``.
        states: boto3 Step Functions client.

    Returns:
        A receipt dict with the run ID, execution ARN, ``status='submitted'``,
        ``acquisition_location='aws-lambda'`` and a console URL.

    Raises:
        ValueError: invalid combinations/bounds (rows must be 1..5,100,000,
            roughly the size of HI-Small; training needs every row).
        RuntimeError: the cloud state machine is not deployed.
    """
    if args.fx_file or args.deployment:
        raise ValueError('Cloud acquisition uses AWS APIs/configuration; use --local-acquisition for local FX/config inputs')
    if args.train and args.rows: raise ValueError('Training requires the full subset')
    if args.rows is not None and not 0 < args.rows <= 5_100_000: raise ValueError('Invalid row bound')
    if args.dataset_version is not None and args.dataset_version < 1: raise ValueError('Invalid dataset version')
    run_id = valid_run(args.run_id or datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(4))
    machine = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:is459-g3-cloud-ingest'
    # Execution input consumed by the cloud acquisition Lambda/workflow.
    event = {'run_id': run_id, 'rows': args.rows, 'dataset_version': args.dataset_version,
             'train': args.train, 'refresh': args.refresh, 'rerun': args.rerun}
    try:
        response = states.start_execution(stateMachineArn=machine, name=run_id, input=json.dumps(event))
    except states.exceptions.StateMachineDoesNotExist:
        raise RuntimeError('Cloud acquisition is not deployed; see planning/archive/reviews/cloud-ingestion-review.md, or explicitly use --local-acquisition') from None
    return {'run_id': run_id, 'execution_arn': response['executionArn'], 'status': 'submitted',
            'acquisition_location': 'aws-lambda', 'console_url':
            f"https://{REGION}.console.aws.amazon.com/states/home?region={REGION}#/executions/details/{response['executionArn']}"}


def status(run_id, states, cloud=None):
    """Look up the Step Functions execution for ``run_id`` and summarise it.

    Args:
        run_id: run ID (= execution name).
        states: boto3 Step Functions client.
        cloud: True to look only in ``is459-g3-cloud-ingest``, False for only
            ``is459-g3-ingest``, None to try cloud first then the batch machine
            (used by the ``status`` sub-command when the route is unknown).

    Returns:
        Dict with ``run_id``, ``status`` (RUNNING/SUCCEEDED/FAILED/TIMED_OUT/
        ABORTED), ``execution_arn``, plus the parsed ``result`` output when
        finished, or ``error``/``cause`` when it failed.

    Raises:
        ExecutionDoesNotExist: if no candidate machine has that execution.
    """
    valid_run(run_id)
    names = ['is459-g3-cloud-ingest', 'is459-g3-ingest'] if cloud is None else [
        'is459-g3-cloud-ingest' if cloud else 'is459-g3-ingest']
    for name in names:
        arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:{name}:{run_id}'
        try:
            result = states.describe_execution(executionArn=arn)
            break
        except states.exceptions.ExecutionDoesNotExist:
            # Try the next state machine; re-raise only after the last one.
            if name == names[-1]: raise
    return {'run_id': run_id, 'status': result['status'], 'execution_arn': arn,
            **({'result': json.loads(result['output'])} if 'output' in result else {}),
            **({'error': result.get('error'), 'cause': result.get('cause')} if 'error' in result else {})}


def main():
    """Parse the command line and dispatch to download/prepare/run/status/unlock.

    Selection and route validation use ``parser.error`` before dispatch.
    Additional input and manifest checks happen during preparation or start;
    a later failure can leave local or uploaded artifacts for inspection. For
    ``run`` the function blocks, printing the execution status every 20
    seconds, until the workflow finishes (unless ``--no-wait``), and raises
    RuntimeError if it did not succeed.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='is459-g3-tools')
    # Sub-commands share options; build them in a loop so they stay consistent.
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ['download', 'prepare', 'run']:
        command = commands.add_parser(action)
        add_selection_arguments(command)
        command.add_argument('--dataset-directory', type=Path, default=Path('dataset'))
        command.add_argument('--dataset-version', type=int)
        command.add_argument('--refresh', action='store_true')
        if action == 'download':
            continue
        # Everything below applies to prepare/run only (they build pipeline inputs).
        command.add_argument('--rows', type=int)
        command.add_argument('--fx-file', type=Path, help='Explicit offline input; default fetches historical APIs')
        command.add_argument('--deployment', type=Path, help='Local deployment JSON for offline preparation')
        command.add_argument('--run-id')
        command.add_argument('--train', action='store_true')
        command.add_argument('--rerun', action='store_true')
        if action == 'run':
            command.add_argument('--no-wait', action='store_true')
            command.add_argument('--local-acquisition', action='store_true', help='Explicit compatibility path: acquire/upload on this computer')
            command.add_argument('--trigger', choices=['direct', 'eventbridge'], default='direct',
                                 help='With --local-acquisition: start the batch directly, or let the S3 upload trigger it')
    # status/unlock only need the run ID of an existing run.
    for action in ['status', 'unlock']:
        commands.add_parser(action).add_argument('--run-id', required=True)
    args = parser.parse_args()
    # ---- Local-only actions and argument validation (no AWS calls yet) ----
    if args.action in ['download', 'prepare', 'run']:
        if args.list_options:
            print(json.dumps(selection_options(), indent=2))
            return
        try:
            args.subset = resolve_subset(args.subset, args.variant, args.size)
            if args.dataset_version is not None and args.dataset_version < 1:
                raise ValueError('Dataset version must be positive')
            # Cloud acquisition downloads HI-Small only; any other subset is acquired locally.
            if args.action != 'download':
                if args.subset != 'HI-Small' and args.action == 'run' and not args.local_acquisition:
                    raise ValueError(f'{args.subset} requires -LocalAcquisition (Python: --local-acquisition)')
                if args.files is not None or args.include_patterns:
                    raise ValueError('File selection and patterns require -DownloadOnly (Python: download); the pipeline needs transactions and accounts')
                if args.rows is not None and args.rows <= 0:
                    raise ValueError('Rows must be positive')
                if args.train and args.rows is not None:
                    raise ValueError('Training requires the full subset; omit --rows')
                if getattr(args, 'trigger', 'direct') == 'eventbridge' and not args.local_acquisition:
                    raise ValueError('-Trigger EventBridge starts from a laptop upload; add -LocalAcquisition')
        except ValueError as error:
            parser.error(str(error))
        # --dry-run: show what would happen without network calls or file writes.
        if args.dry_run:
            print(json.dumps({'action': args.action, 'subset': args.subset,
                              'acquisition_location': 'aws-lambda' if args.action == 'run' and not args.local_acquisition else 'local',
                              'directory': str(args.dataset_directory.resolve()),
                              'files': selected_files(args.subset, args.files, args.include_patterns),
                              'dataset_version': args.dataset_version, 'refresh': args.refresh,
                              **({key: getattr(args, key) for key in ['rows', 'train', 'rerun']} if args.action != 'download' else {}),
                              'dry_run': True, 'starts_aws_workflow': False,
                              'would_start_aws_workflow': args.action == 'run'}, indent=2))
            return
        if args.action == 'download':
            from bda_pipeline.acquisition import acquire_dataset
            record = acquire_dataset(args.dataset_directory, args.subset, args.dataset_version, args.refresh,
                                     include_patterns=args.include_patterns, files=args.files)
            print(json.dumps({'status': 'downloaded-not-submitted', 'directory': str(args.dataset_directory.resolve()),
                              **record}, indent=2))
            return
    # ---- Actions that talk to AWS ----
    s3 = states = None
    if args.action in ['status', 'unlock']:
        session, s3, states = aws_clients(args.profile)
        # unlock: the control Lambda's 'recover' op releases pipeline/lock.json
        # only if the execution is in a terminal failed state AND no Glue or
        # SageMaker worker for that run is still active. Outputs are kept.
        if args.action == 'unlock':
            response = session.client('lambda').invoke(FunctionName='is459-g3-ingest-control',
                Payload=canonical({'op': 'recover', 'run_id': valid_run(args.run_id)}))
            # A Lambda that raised still returns 200; FunctionError signals the failure.
            with response['Payload'] as body: result = json.loads(body.read())
            if response.get('FunctionError'): raise ValueError(result)
        else: result = status(args.run_id, states)
        print(json.dumps(result, indent=2))
        return
    # Default run route: acquisition happens in AWS; poll every 20 s until done.
    if args.action == 'run' and not args.local_acquisition:
        _, _, states = aws_clients(args.profile)
        receipt = cloud_start(args, states)
        print(json.dumps(receipt, indent=2), flush=True)
        if args.no_wait: return
        while True:
            result = status(receipt['run_id'], states, cloud=True)
            print(json.dumps(result), flush=True)
            if result['status'] != 'RUNNING':
                if result['status'] != 'SUCCEEDED': raise RuntimeError('Cloud acquisition/batch failed; inspect evidence before retrying')
                return
            time.sleep(20)
    # Local-acquisition route (prepare, or run --local-acquisition). The
    # deployed config is needed because its hash goes into the manifest; an
    # offline 'prepare --deployment file.json' can use a local copy instead.
    if args.action == 'prepare' and args.deployment:
        config = json.loads(args.deployment.read_text(encoding='utf-8'))
    else:
        _, s3, states = aws_clients(args.profile)
        config = read_json(s3, CONFIG_KEY)
        if config is None:
            raise ValueError('Not deployed; use prepare --deployment for local checks')
        # If a local config was also given, it must match what is deployed.
        if args.deployment and sha(json.loads(args.deployment.read_text())) != sha(config):
            raise ValueError('Local and deployed configurations differ')
    manifest, output = prepare(args, config)
    if args.action == 'prepare':
        print(json.dumps({'status': 'prepared-not-submitted', 'run_id': manifest['run_id'], 'directory': str(output)}))
        return
    # Upload + start; a cache hit returns immediately with the old result.
    receipt = start(manifest, output, s3, states, getattr(args, 'trigger', 'direct'))
    print(json.dumps(receipt, indent=2), flush=True)
    if receipt.get('cache_hit') or args.no_wait: return
    # Poll the batch machine every 20 s until it leaves RUNNING.
    while True:
        result = status(manifest['run_id'], states, cloud=False)
        print(json.dumps(result), flush=True)
        if result['status'] != 'RUNNING':
            if result['status'] != 'SUCCEEDED':
                raise RuntimeError('Cloud workflow failed; inspect evidence before retrying')
            return
        time.sleep(20)


if __name__ == '__main__': main()
