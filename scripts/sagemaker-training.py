"""Prepare, explicitly submit, inspect and collect one bounded SageMaker training job."""
import argparse
import json
import os
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse

from bda_pipeline.model import SavedRanker, digest
from bda_pipeline.sagemaker_training import (
    ACCOUNT, BUCKET, REGION, prepare_bundle, validate_bundle,
)


def clients(profile):
    import boto3
    from botocore.config import Config
    os.environ['AWS_SDK_UA_APP_ID'] = 'AWSSkill-SageMaker'
    session = boto3.Session(profile_name=profile, region_name=REGION)
    config = Config(retries={'mode': 'standard', 'total_max_attempts': 3},
                    connect_timeout=10, read_timeout=60, user_agent_appid='AWSSkill-SageMaker')
    identity = session.client('sts', config=config).get_caller_identity()
    if identity['Account'] != ACCOUNT:
        raise ValueError('Wrong AWS account')
    # Submission has no SDK retry: a lost acknowledgement must be inspected by job name.
    mutation_config = Config(retries={'mode': 'standard', 'total_max_attempts': 1},
                             connect_timeout=10, read_timeout=60, user_agent_appid='AWSSkill-SageMaker')
    return session.client('s3', config=config), session.client('sagemaker', config=mutation_config)


def submit(directory, s3, sagemaker):
    request = validate_bundle(directory)
    directory = Path(directory)
    name = request['TrainingJobName']
    prefix = f'sagemaker/inputs/{name}'
    # A conditional claim prevents a second invocation from overwriting the staged input.
    s3.put_object(Bucket=BUCKET, Key=f'{prefix}/claim.json', IfNoneMatch='*',
                  Body=json.dumps({'request_sha256': digest(directory / 'request.json')}).encode(),
                  ServerSideEncryption='AES256', ContentType='application/json')
    for channel, filename in [('baseline', 'bq1_scored.parquet'),
                              ('manifest', 'dashboard.json'), ('manifest', 'input.json')]:
        s3.upload_file(str(directory / channel / filename), BUCKET, f'{prefix}/{channel}/{filename}',
                       ExtraArgs={'ServerSideEncryption': 'AES256'})
    response = sagemaker.create_training_job(**request)
    receipt = {'job_name': name, 'training_job_arn': response['TrainingJobArn'],
               'request_sha256': digest(directory / 'request.json'),
               'status': 'submitted', 'model_promoted': False}
    (directory / 'submission.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def extract_model(archive, output):
    """Accept only bounded, flat regular files; never extract tar links or paths."""
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh collection directory')
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        files = []
        for member in members:
            if member.isdir() and member.name in ['.', './']:
                continue
            name = member.name.removeprefix('./')
            if (not member.isfile() or Path(name).name != name or '\\' in name
                    or name in ['', '.', '..'] or Path(name).suffix not in ['.json', '.ubj', '.csv']):
                raise ValueError('Unsafe model archive member')
            files.append((member, name))
        if (not files or len(files) > 20 or sum(member.size for member, _ in files) > 100_000_000
                or len({name for _, name in files}) != len(files)):
            raise ValueError('Oversized or duplicate model archive')
        output.mkdir(parents=True)
        for member, name in files:
            with tar.extractfile(member) as source, (output / name).open('wb') as target:
                import shutil
                shutil.copyfileobj(source, target)


def collect(directory, output, s3, sagemaker):
    request = validate_bundle(directory)
    description = sagemaker.describe_training_job(TrainingJobName=request['TrainingJobName'])
    if description['TrainingJobStatus'] != 'Completed':
        raise ValueError(f"Job has not completed: {description['TrainingJobStatus']}")
    def contains(actual, expected):
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(key in actual and contains(actual[key], value)
                                                    for key, value in expected.items())
        if isinstance(expected, list):
            return (isinstance(actual, list) and len(actual) == len(expected)
                    and all(contains(a, b) for a, b in zip(actual, expected)))
        return actual == expected
    for field in ['RoleArn', 'HyperParameters', 'ResourceConfig', 'InputDataConfig',
                  'StoppingCondition', 'OutputDataConfig']:
        if not contains(description[field], request[field]):
            raise ValueError(f'Completed job contract mismatch: {field}')
    if (description['AlgorithmSpecification']['TrainingImage'] != request['AlgorithmSpecification']['TrainingImage']
            or not description['EnableNetworkIsolation']):
        raise ValueError('Completed job image or isolation differs from request')
    location = urlparse(description['ModelArtifacts']['S3ModelArtifacts'])
    expected_key = f"sagemaker/outputs/{request['TrainingJobName']}/output/model.tar.gz"
    if location.scheme != 's3' or location.netloc != BUCKET or location.path.lstrip('/') != expected_key:
        raise ValueError('Unexpected model artifact location')
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh collection directory')
    archive = Path(directory) / 'model.tar.gz'
    s3.download_file(BUCKET, expected_key, str(archive))
    extract_model(archive, output)
    ranker = SavedRanker(output)
    report = ranker.report
    provenance = json.loads((Path(directory) / 'manifest/input.json').read_text(encoding='utf-8'))
    execution = report['execution']
    if (report['total_rows'] != provenance['rows']
            or execution['job_name'] != request['TrainingJobName']
            or execution['image_uri'] != request['AlgorithmSpecification']['TrainingImage']
            or execution['scored_sha256'] != provenance['scored_sha256']
            or execution['snapshot_sha256'] != provenance['snapshot_sha256']):
        raise ValueError('Model provenance mismatch')
    result = {'status': 'completed-artifacts-verified-candidate-only',
              'job_name': request['TrainingJobName'], 'model_promoted': False,
              'model_sha256': ranker.selection['selected']['sha256'],
              'comparison_sha256': digest(output / 'comparison.json'),
              'validation_gate_passed': ranker.selection['validation_gate_passed'],
              'training_seconds': description.get('TrainingTimeInSeconds'),
              'billable_seconds': description.get('BillableTimeInSeconds')}
    (output / 'aws-training-result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='is459-g3-tools')
    commands = parser.add_subparsers(dest='action', required=True)
    prepare = commands.add_parser('prepare', help='Local preparation only; no AWS calls')
    prepare.add_argument('--scored', type=Path, required=True)
    prepare.add_argument('--snapshot', type=Path, required=True)
    prepare.add_argument('--output', type=Path, required=True)
    prepare.add_argument('--job-name', required=True)
    prepare.add_argument('--image-uri', required=True)
    prepare.add_argument('--rounds', type=int, default=120)
    prepare.add_argument('--max-runtime', type=int, default=1200)
    start = commands.add_parser('start', help='Upload inputs and start one paid job')
    start.add_argument('--prepared', type=Path, required=True)
    start.add_argument('--execute', action='store_true', help='Required to start paid training')
    status = commands.add_parser('status', help='Read-only job status')
    status.add_argument('--prepared', type=Path, required=True)
    download = commands.add_parser('collect', help='Download and verify completed model artifacts')
    download.add_argument('--prepared', type=Path, required=True)
    download.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        request = prepare_bundle(args.scored, args.snapshot, args.output, args.job_name,
                                 args.image_uri, args.rounds, args.max_runtime)
        print(json.dumps({'status': 'prepared-not-submitted', 'job_name': request['TrainingJobName']}))
        return 0
    if args.action == 'start' and not args.execute:
        parser.error('start requires --execute; preparation and status do not start compute')
    request = validate_bundle(args.prepared)
    s3, sagemaker = clients(args.profile)
    if args.action == 'start':
        result = submit(args.prepared, s3, sagemaker)
    elif args.action == 'collect':
        result = collect(args.prepared, args.output, s3, sagemaker)
    else:
        job = sagemaker.describe_training_job(TrainingJobName=request['TrainingJobName'])
        result = {key: job.get(key) for key in ['TrainingJobName', 'TrainingJobStatus',
                    'SecondaryStatus', 'FailureReason', 'TrainingTimeInSeconds', 'BillableTimeInSeconds']}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
