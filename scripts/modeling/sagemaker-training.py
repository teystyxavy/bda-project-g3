"""Prepare, explicitly submit, inspect and collect one bounded SageMaker training job.

Where this sits in the pipeline
-------------------------------
BQ1's XGBoost risk model can be trained on a laptop
(``compare-local-model.py``) or as a managed Amazon SageMaker training job on
one ml.m5.xlarge instance. This CLI drives the SageMaker route in four
explicit, separately-run steps, so paid compute is never started by accident:

``prepare`` (local only)
    ``sagemaker_training.prepare_bundle`` copies the full-population
    ``bq1_scored.parquet`` and verified ``dashboard.json`` into a fresh bundle
    folder, records their hashes in ``manifest/input.json`` and writes the
    exact ``CreateTrainingJob`` request to ``request.json`` (pinned image
    digest, fixed role/bucket, network isolation, max rounds/runtime).
``start --execute`` (paid)
    Re-validates the bundle, claims the S3 input prefix, uploads the two input
    channels to ``s3://<BUCKET>/sagemaker/inputs/<job>/`` and calls
    ``CreateTrainingJob``. Without ``--execute`` it refuses to run.
``status`` (read-only)
    Prints the job's status, failure reason and training/billable seconds.
``collect`` (read-only in AWS)
    Checks the completed job matches the request, downloads
    ``sagemaker/outputs/<job>/output/model.tar.gz``, safely extracts it, loads
    the model and verifies its provenance (rows, job name, image, input
    hashes). The result is only a *candidate* - ``model_promoted`` is always
    False; promotion is a separate, reviewed step.

Usage
-----
    python scripts/modeling/sagemaker-training.py prepare --scored ... \
        --snapshot .../dashboard.json --output output/train-job-1 \
        --job-name is459-g3-train-job-1 --image-uri <ecr>@sha256:<digest>
    python scripts/modeling/sagemaker-training.py start --prepared output/train-job-1 --execute
    python scripts/modeling/sagemaker-training.py status --prepared output/train-job-1
    python scripts/modeling/sagemaker-training.py collect --prepared output/train-job-1 \
        --output output/train-job-1-model
"""
import argparse
import json
import os
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse

from bda_pipeline.common.integrity import digest
from bda_pipeline.modeling.model import SavedRanker
from bda_pipeline.modeling.sagemaker_training import (
    ACCOUNT, BUCKET, REGION, prepare_bundle, validate_bundle,
)


def clients(profile):
    """Create S3 and SageMaker clients for the project account.

    boto3 is imported lazily so ``prepare`` works without AWS libraries.

    Args:
        profile: named AWS CLI profile.

    Returns:
        ``(s3_client, sagemaker_client)``. S3 uses normal retries (max 3
        attempts); the SageMaker client makes exactly one attempt (see the
        comment below for why).

    Raises:
        ValueError: if the credentials are for a different AWS account.
    """
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
    """Upload a validated bundle and start the paid training job.

    Args:
        directory: prepared bundle folder.
        s3: boto3 S3 client.
        sagemaker: boto3 SageMaker client (single-attempt).

    Returns:
        The submission receipt (job name, ARN, request hash,
        ``status='submitted'``, ``model_promoted=False``), also saved as
        ``<directory>/submission.json``.

    Side effects:
        Writes ``claim.json`` (create-only) and the input files under
        ``s3://<BUCKET>/sagemaker/inputs/<job>/`` with SSE-S3 encryption, then
        starts one SageMaker training job (billed per second).
    """
    # Re-check the bundle: inputs unchanged and request.json identical to a
    # freshly built request (catches hand edits such as a bigger instance).
    request = validate_bundle(directory)
    directory = Path(directory)
    name = request['TrainingJobName']
    prefix = f'sagemaker/inputs/{name}'
    # A conditional claim prevents a second invocation from overwriting the staged input.
    s3.put_object(Bucket=BUCKET, Key=f'{prefix}/claim.json', IfNoneMatch='*',
                  Body=json.dumps({'request_sha256': digest(directory / 'request.json')}).encode(),
                  ServerSideEncryption='AES256', ContentType='application/json')
    # Two input channels; SageMaker mounts them in the container at
    # /opt/ml/input/data/baseline and /opt/ml/input/data/manifest.
    for channel, filename in [('baseline', 'bq1_scored.parquet'),
                              ('manifest', 'dashboard.json'), ('manifest', 'input.json')]:
        s3.upload_file(str(directory / channel / filename), BUCKET, f'{prefix}/{channel}/{filename}',
                       ExtraArgs={'ServerSideEncryption': 'AES256'})
    # The request is passed exactly as reviewed in request.json.
    response = sagemaker.create_training_job(**request)
    receipt = {'job_name': name, 'training_job_arn': response['TrainingJobArn'],
               'request_sha256': digest(directory / 'request.json'),
               'status': 'submitted', 'model_promoted': False}
    (directory / 'submission.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def extract_model(archive, output):
    """Accept only bounded, flat regular files; never extract tar links or paths.

    ``tarfile.extractall`` on an untrusted archive can write outside the
    target folder ("path traversal", e.g. ``../../x``) or create symlinks.
    This function instead validates every member and copies the file
    contents itself.

    Rules: only regular files (no links, devices or sub-folders), plain
    names with no directory part or backslash, extensions ``.json``,
    ``.ubj`` (XGBoost binary model) or ``.csv``; 1-20 files, at most 100 MB
    in total, no duplicate names.

    Args:
        archive: path to ``model.tar.gz``.
        output: fresh folder to extract into (must not exist).

    Raises:
        ValueError: if any rule is broken (nothing is extracted then).
    """
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh collection directory')
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        files = []
        for member in members:
            # The archive's own root entry ('.' or './') is harmless; skip it.
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
        # Only after every member passed: create the folder and stream each file.
        output.mkdir(parents=True)
        for member, name in files:
            with tar.extractfile(member) as source, (output / name).open('wb') as target:
                import shutil
                shutil.copyfileobj(source, target)


def collect(directory, output, s3, sagemaker):
    """Verify a completed training job and download/verify its model artifacts.

    Checks, in order:
        1. The job status is ``Completed``.
        2. The job AWS actually ran matches ``request.json``: role,
           hyperparameters, resources, inputs, stopping condition, output
           location, training image, and network isolation enabled.
        3. The model artifact is exactly at
           ``s3://<BUCKET>/sagemaker/outputs/<job>/output/model.tar.gz``.
        4. After safe extraction, the model's own report agrees with the
           bundle's provenance (row count, job name, image, input hashes).

    Args:
        directory: prepared bundle folder (``model.tar.gz`` is downloaded here).
        output: fresh folder for the extracted model files.
        s3: boto3 S3 client.
        sagemaker: boto3 SageMaker client.

    Returns:
        Result dict (``status='completed-artifacts-verified-candidate-only'``,
        model/comparison hashes, whether the validation gate passed, training
        and billable seconds), also written to ``aws-training-result.json``.

    Raises:
        ValueError: on any mismatch above or an unsafe archive.
    """
    request = validate_bundle(directory)
    description = sagemaker.describe_training_job(TrainingJobName=request['TrainingJobName'])
    if description['TrainingJobStatus'] != 'Completed':
        raise ValueError(f"Job has not completed: {description['TrainingJobStatus']}")
    # Recursive "is expected a subset of actual?" check: DescribeTrainingJob
    # returns extra fields AWS fills in (defaults), so exact equality would
    # fail; every field WE asked for must still be present with our value.
    # Lists must match element by element and in length.
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
    # Exact image digest, and the container must have had no network access.
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
    # Loading via SavedRanker also verifies the model artifacts' hashes.
    ranker = SavedRanker(output)
    report = ranker.report
    provenance = json.loads((Path(directory) / 'manifest/input.json').read_text(encoding='utf-8'))
    execution = report['execution']
    # Provenance: the model must say it was trained by THIS job, with THIS
    # image, on exactly the inputs recorded at prepare time.
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
    """Parse the sub-command and run prepare / start / status / collect.

    Returns:
        0 on success (used as the process exit code).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='default')
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
    # prepare is purely local and needs no AWS clients.
    if args.action == 'prepare':
        request = prepare_bundle(args.scored, args.snapshot, args.output, args.job_name,
                                 args.image_uri, args.rounds, args.max_runtime)
        print(json.dumps({'status': 'prepared-not-submitted', 'job_name': request['TrainingJobName']}))
        return 0
    # Paid compute requires an explicit --execute flag.
    if args.action == 'start' and not args.execute:
        parser.error('start requires --execute; preparation and status do not start compute')
    # Every AWS action first re-validates the local bundle.
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
