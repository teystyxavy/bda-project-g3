"""One-command source-to-dashboard entry point; run starts the configured paid workflow."""
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
    import boto3
    from botocore.config import Config
    os.environ['AWS_SDK_UA_APP_ID'] = 'AWSSkill-SageMaker'
    session = boto3.Session(profile_name=profile, region_name=REGION)
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3},
                    connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    if session.client('sts', config=config).get_caller_identity()['Account'] != ACCOUNT:
        raise ValueError('Wrong AWS account')
    return session, session.client('s3', config=config), session.client('stepfunctions', config=config)


def read_json(s3, key):
    listing = s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1)
    if not any(item['Key'] == key for item in listing.get('Contents', [])): return None
    try:
        response = s3.get_object(Bucket=BUCKET, Key=key)
    except s3.exceptions.NoSuchKey:
        return None
    with response['Body'] as body:
        return json.loads(body.read())


def prepare(args, config):
    from bda_pipeline.acquisition import acquire_dataset, prepare_inputs
    if args.train and args.rows:
        raise ValueError('Training needs the full subset and chronological splits; omit --rows')
    record = acquire_dataset(args.dataset_directory, version=args.dataset_version, refresh=args.refresh)
    run_id = valid_run(args.run_id or datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(4))
    output = Path('output/ingest') / run_id
    info = prepare_inputs(args.dataset_directory / 'HI-Small_Trans.csv',
                          args.dataset_directory / 'HI-Small_accounts.csv', output,
                          rows=args.rows, fx_file=args.fx_file)
    identity = {**{key: info[key] for key in ['rows', 'files', 'start_date', 'end_date']},
                'subset': 'HI-Small', 'dataset_version': record['dataset_version'],
                'deployment_sha256': sha(config), 'train': args.train}
    manifest = {**identity, 'schema_version': 1, 'run_id': run_id,
                'processing_sha256': sha(identity), 'rerun': args.rerun,
                'source_scope': info['scope'], 'currencies': info['currencies'],
                'dataset_origin': record['origin'], 'created_at': datetime.now(timezone.utc).isoformat(),
                'model_manifest_sha256': config['model_manifest_sha256']}
    validate_manifest(manifest, sha(config))
    (output / 'manifest.json').write_bytes(canonical(manifest))
    print(f"Prepared {info['rows']:,} rows for {run_id}; historical FX {info['start_date']} to {info['end_date']}", flush=True)
    return manifest, output


def await_trigger(run_id, states, timeout=120):
    arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:is459-g3-ingest:{run_id}'
    deadline = time.monotonic() + timeout
    while True:
        try:
            return states.describe_execution(executionArn=arn)['executionArn']
        except states.exceptions.ExecutionDoesNotExist:
            if time.monotonic() > deadline:
                raise RuntimeError('EventBridge did not start the batch; check that infra/eventbridge-trigger is deployed '
                                   'and inspect is459-g3-raw-trigger executions. Uploaded inputs are kept.') from None
            time.sleep(5)


def start(manifest, output, s3, states, trigger='direct'):
    if not manifest['rerun']:
        cached = read_json(s3, f"pipeline/cache/{manifest['processing_sha256']}.json")
        if cached and cached.get('status') == 'published':
            return {**cached, 'cache_hit': True}
    p = paths(manifest['run_id'])
    s3.put_object(Bucket=BUCKET, Key=f"{p['raw']}/upload-claim.json", IfNoneMatch='*',
                  Body=canonical({'run_id': manifest['run_id']}), ServerSideEncryption='AES256')
    for name, info in manifest['files'].items():
        s3.upload_file(str(output / name), BUCKET, f"{p['raw']}/{name}",
                       ExtraArgs={'ServerSideEncryption': 'AES256', 'Metadata': {'sha256': info['sha256']}})
    s3.put_object(Bucket=BUCKET, Key=f"{p['raw']}/manifest.json", Body=canonical(manifest),
                  IfNoneMatch='*', ServerSideEncryption='AES256', ContentType='application/json')
    if trigger == 'eventbridge':
        # Written last: the EventBridge rule matches only this key, so the inputs are complete when it fires.
        s3.put_object(Bucket=BUCKET, Key=f"{p['raw']}/trigger.json", IfNoneMatch='*', ServerSideEncryption='AES256',
                      Body=canonical({'run_id': manifest['run_id'], 'requested_at': datetime.now(timezone.utc).isoformat()}),
                      ContentType='application/json')
        execution_arn = await_trigger(manifest['run_id'], states)
    else:
        execution_arn = states.start_execution(stateMachineArn=MACHINE, name=manifest['run_id'],
            input=json.dumps({'run_id': manifest['run_id']}))['executionArn']
    receipt = {'run_id': manifest['run_id'], 'execution_arn': execution_arn,
               'status': 'submitted', 'started_by': trigger, 'console_url':
               f"https://{REGION}.console.aws.amazon.com/states/home?region={REGION}#/executions/details/{execution_arn}"}
    (output / 'submission.json').write_bytes(canonical(receipt))
    return receipt


def cloud_start(args, states):
    if args.fx_file or args.deployment:
        raise ValueError('Cloud acquisition uses AWS APIs/configuration; use --local-acquisition for local FX/config inputs')
    if args.train and args.rows: raise ValueError('Training requires the full subset')
    if args.rows is not None and not 0 < args.rows <= 5_100_000: raise ValueError('Invalid row bound')
    if args.dataset_version is not None and args.dataset_version < 1: raise ValueError('Invalid dataset version')
    run_id = valid_run(args.run_id or datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-') + secrets.token_hex(4))
    machine = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:is459-g3-cloud-ingest'
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
    valid_run(run_id)
    names = ['is459-g3-cloud-ingest', 'is459-g3-ingest'] if cloud is None else [
        'is459-g3-cloud-ingest' if cloud else 'is459-g3-ingest']
    for name in names:
        arn = f'arn:aws:states:{REGION}:{ACCOUNT}:execution:{name}:{run_id}'
        try:
            result = states.describe_execution(executionArn=arn)
            break
        except states.exceptions.ExecutionDoesNotExist:
            if name == names[-1]: raise
    return {'run_id': run_id, 'status': result['status'], 'execution_arn': arn,
            **({'result': json.loads(result['output'])} if 'output' in result else {}),
            **({'error': result.get('error'), 'cause': result.get('cause')} if 'error' in result else {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='is459-g3-tools')
    commands = parser.add_subparsers(dest='action', required=True)
    for action in ['download', 'prepare', 'run']:
        command = commands.add_parser(action)
        add_selection_arguments(command)
        command.add_argument('--dataset-directory', type=Path, default=Path('dataset'))
        command.add_argument('--dataset-version', type=int)
        command.add_argument('--refresh', action='store_true')
        if action == 'download':
            continue
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
    for action in ['status', 'unlock']:
        commands.add_parser(action).add_argument('--run-id', required=True)
    args = parser.parse_args()
    if args.action in ['download', 'prepare', 'run']:
        if args.list_options:
            print(json.dumps(selection_options(), indent=2))
            return
        try:
            args.subset = resolve_subset(args.subset, args.variant, args.size)
            if args.dataset_version is not None and args.dataset_version < 1:
                raise ValueError('Dataset version must be positive')
            if args.action != 'download':
                if args.subset != 'HI-Small':
                    raise ValueError('The pipeline supports HI-Small only; use -DownloadOnly (Python: download) for other subsets')
                if args.files is not None or args.include_patterns:
                    raise ValueError('File selection and patterns require -DownloadOnly (Python: download); the pipeline needs transactions and accounts')
                if args.rows is not None and not 0 < args.rows <= 5_100_000:
                    raise ValueError('Rows must be between 1 and 5,100,000')
                if args.train and args.rows is not None:
                    raise ValueError('Training requires the full subset; omit --rows')
                if getattr(args, 'trigger', 'direct') == 'eventbridge' and not args.local_acquisition:
                    raise ValueError('-Trigger EventBridge starts from a laptop upload; add -LocalAcquisition')
        except ValueError as error:
            parser.error(str(error))
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
    s3 = states = None
    if args.action in ['status', 'unlock']:
        session, s3, states = aws_clients(args.profile)
        if args.action == 'unlock':
            response = session.client('lambda').invoke(FunctionName='is459-g3-ingest-control',
                Payload=canonical({'op': 'recover', 'run_id': valid_run(args.run_id)}))
            with response['Payload'] as body: result = json.loads(body.read())
            if response.get('FunctionError'): raise ValueError(result)
        else: result = status(args.run_id, states)
        print(json.dumps(result, indent=2))
        return
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
    if args.action == 'prepare' and args.deployment:
        config = json.loads(args.deployment.read_text(encoding='utf-8'))
    else:
        _, s3, states = aws_clients(args.profile)
        config = read_json(s3, CONFIG_KEY)
        if config is None:
            raise ValueError('Not deployed; use prepare --deployment for local checks')
        if args.deployment and sha(json.loads(args.deployment.read_text())) != sha(config):
            raise ValueError('Local and deployed configurations differ')
    manifest, output = prepare(args, config)
    if args.action == 'prepare':
        print(json.dumps({'status': 'prepared-not-submitted', 'run_id': manifest['run_id'], 'directory': str(output)}))
        return
    receipt = start(manifest, output, s3, states, getattr(args, 'trigger', 'direct'))
    print(json.dumps(receipt, indent=2), flush=True)
    if receipt.get('cache_hit') or args.no_wait: return
    while True:
        result = status(manifest['run_id'], states, cloud=False)
        print(json.dumps(result), flush=True)
        if result['status'] != 'RUNNING':
            if result['status'] != 'SUCCEEDED':
                raise RuntimeError('Cloud workflow failed; inspect evidence before retrying')
            return
        time.sleep(20)


if __name__ == '__main__': main()
