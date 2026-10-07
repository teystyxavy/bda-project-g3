"""Bounded SageMaker job contracts and a reusable, offline training entry point.

Purpose
-------
The XGBoost comparison in ``model.compare_models`` can be run on a laptop, or
as a managed Amazon SageMaker training job. This module holds everything about
the SageMaker job that does *not* itself call AWS:

* ``prepare_bundle`` / ``validate_bundle`` / ``build_request`` - run on the
  developer's machine by ``scripts/modeling/sagemaker-training.py``. They copy
  and hash the inputs into a local "bundle" folder and build the exact
  ``CreateTrainingJob`` request. That script then uploads the bundle to S3 and
  submits the request (only with an explicit ``start --execute``).
* ``train_channels`` - runs *inside* the training container
  (``sagemaker/train.py``, the ENTRYPOINT of ``sagemaker/Dockerfile``) and
  calls ``compare_models`` on the files SageMaker has copied in from S3.

"Bounded contract" means every job is forced into a small, safe, predictable
shape so a typo cannot launch an expensive or insecure job: fixed account,
region, bucket, IAM role and ECR repository; image pinned by SHA-256 digest;
one ml.m5.xlarge instance; at most 120 boosting rounds and 30 minutes; network
isolation on; and the resulting model is only a *candidate* - nothing here
promotes it to production or replaces the dashboard.

S3 layout used by a job named ``<job>``::

    s3://is459-g3-<account>-ap-southeast-1/sagemaker/inputs/<job>/baseline/
        bq1_scored.parquet           (baseline-scored curated dataset)
    s3://.../sagemaker/inputs/<job>/manifest/
        dashboard.json, input.json   (verified snapshot + provenance)
    s3://.../sagemaker/outputs/      (SageMaker writes model.tar.gz here)

Inside the container SageMaker mounts those channels at
``/opt/ml/input/data/baseline`` and ``/opt/ml/input/data/manifest``, and
uploads whatever is left in ``/opt/ml/model`` and ``/opt/ml/output/data``.
"""
import json
import re
import shutil
from pathlib import Path

import pyarrow.dataset as ds

from .model import SavedRanker, compare_models, digest

# Fixed deployment target for Group 3. Hard-coding these (instead of reading
# them from the environment) means the request can only ever target the
# project's own account, region, bucket, role and image repository.
ACCOUNT = '588738581042'
REGION = 'ap-southeast-1'
BUCKET = f'is459-g3-{ACCOUNT}-{REGION}'
# IAM role SageMaker assumes for the job; it needs read access to the input
# prefix, write access to the output prefix, and ECR pull for the image.
ROLE = f'arn:aws:iam::{ACCOUNT}:role/is459-g3-sagemaker-training'
REPOSITORY = f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/is459-g3-training'


def validate_name(name):
    """Enforce the job naming rule ``is459-g3-train-<suffix>``.

    The suffix is lowercase letters, digits and hyphens, must start and end
    with a letter/digit, and the full name is at most 63 characters
    (SageMaker's limit). The fixed prefix also lets IAM policies scope
    permissions to ``is459-g3-train-*`` jobs only.

    Raises:
        ValueError: if the name does not match.
    """
    # 15-char prefix + 1 + up to 46 + 1 = 63 characters maximum.
    if not re.fullmatch(r'is459-g3-train-[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?', name):
        raise ValueError('Use a safe is459-g3-train- job name of at most 63 characters')


def validate_inputs(scored, snapshot):
    """Prove the training input is the verified full-population baseline export.

    Args:
        scored: Path to ``bq1_scored.parquet``.
        snapshot: Path to the dashboard snapshot ``dashboard.json``, which
            records the scored file's SHA-256 and the full row count.

    Returns:
        ``{'rows', 'scored_sha256', 'snapshot_sha256'}`` - the provenance
        facts later written to ``manifest/input.json`` and re-checked inside
        the container.

    Raises:
        ValueError: if the snapshot is not full-scope, the parquet hash
            differs, or the row count is zero or differs from the snapshot.
    """
    snapshot_data = json.loads(Path(snapshot).read_text(encoding='utf-8'))
    # Find the hash the snapshot recorded for bq1_scored.parquet (None if absent).
    expected = next((item['sha256'] for item in snapshot_data['sources']
                     if item['file'] == 'bq1_scored.parquet'), None)
    if snapshot_data.get('aml_scope') != 'full' or digest(scored) != expected:
        raise ValueError('Requires a hash-verified full-population baseline export')
    # count_rows reads Parquet metadata only, so it is cheap even for large files.
    rows = ds.dataset(scored, format='parquet').count_rows()
    if rows <= 0 or rows != snapshot_data['full_rows']:
        raise ValueError('Input row count mismatch')
    return {'rows': rows, 'scored_sha256': expected, 'snapshot_sha256': digest(snapshot)}


def build_request(job_name, image_uri, rows, rounds=120, max_runtime=1200):
    """Build the exact SageMaker ``CreateTrainingJob`` request dict.

    Args:
        job_name: Validated with ``validate_name``.
        image_uri: Training image; must be in the Group 3 ECR repository and
            pinned by digest (``...@sha256:<64 hex>``), so the job runs exactly
            the reviewed image rather than whatever a mutable tag points to.
        rows: Expected input row count, passed to the container as a
            hyperparameter so it can double-check the staged data.
        rounds: Max boosting rounds (1-120).
        max_runtime: Hard time limit in seconds (60-1800); SageMaker stops the
            job (and billing) when it is reached.

    Returns:
        A dict that can be passed as ``sagemaker.create_training_job(**request)``.

    Key settings in the request:
        * File input mode: SageMaker copies each S3 channel to local disk
          before the container starts.
        * Two channels, ``baseline`` and ``manifest``, both FullyReplicated
          (only one instance anyway).
        * ml.m5.xlarge (4 vCPU, 16 GiB), 1 instance, 20 GB volume.
        * Hyperparameters are strings (a SageMaker requirement); threads=4
          matches the instance's vCPUs.
        * ``EnableNetworkIsolation``: the container has no network access at
          all, so training code cannot download anything or leak data.
        * Inter-container encryption on; spot training off (no interrupted
          runs to reason about); profiler off (no extra cost/resources).
        * Project/Component tags for cost tracking.

    Raises:
        ValueError: if any argument is outside the bounded contract.
    """
    validate_name(job_name)
    if not re.fullmatch(re.escape(REPOSITORY) + r'@sha256:[0-9a-f]{64}', image_uri):
        raise ValueError('Pin a SHA256 digest in the Group 3 training ECR repository')
    # bool is a subclass of int in Python, so True would otherwise pass as 1.
    if not isinstance(rows, int) or isinstance(rows, bool) or rows <= 0:
        raise ValueError('Expected rows must be positive')
    if not 1 <= rounds <= 120 or not 60 <= max_runtime <= 1800:
        raise ValueError('Allow 1-120 rounds and a 60-1800 second runtime')
    # Inputs for each job live under their own prefix, named after the job.
    prefix = f's3://{BUCKET}/sagemaker/inputs/{job_name}'
    return {
        'TrainingJobName': job_name, 'RoleArn': ROLE,
        'AlgorithmSpecification': {'TrainingImage': image_uri, 'TrainingInputMode': 'File'},
        'InputDataConfig': [
            {'ChannelName': channel, 'DataSource': {'S3DataSource': {
                'S3DataType': 'S3Prefix', 'S3Uri': f'{prefix}/{channel}/',
                'S3DataDistributionType': 'FullyReplicated'}}, 'InputMode': 'File'}
            for channel in ['baseline', 'manifest']],
        'OutputDataConfig': {'S3OutputPath': f's3://{BUCKET}/sagemaker/outputs'},
        'ResourceConfig': {'InstanceType': 'ml.m5.xlarge', 'InstanceCount': 1, 'VolumeSizeInGB': 20},
        'StoppingCondition': {'MaxRuntimeInSeconds': max_runtime},
        'HyperParameters': {'rounds': str(rounds), 'threads': '4', 'expected_rows': str(rows)},
        'EnableNetworkIsolation': True, 'EnableInterContainerTrafficEncryption': True,
        'EnableManagedSpotTraining': False,
        'ProfilerConfig': {'DisableProfiler': True},
        'Tags': [{'Key': 'Project', 'Value': 'is459-g3'},
                 {'Key': 'Component', 'Value': 'SageMakerTraining'}],
    }


def prepare_bundle(scored, snapshot, output, job_name, image_uri, rounds=120, max_runtime=1200):
    """Create the local bundle folder that will be uploaded for one job.

    Layout written under ``output`` (must not exist yet)::

        baseline/bq1_scored.parquet   -> uploaded as the "baseline" channel
        manifest/dashboard.json       -> uploaded as the "manifest" channel
        manifest/input.json           provenance: hashes, rows, job, image
        request.json                  the CreateTrainingJob request

    Nothing is uploaded or started here (status ``prepared-not-submitted``).

    Args:
        scored, snapshot: Verified inputs (see ``validate_inputs``).
        output: New bundle directory.
        job_name, image_uri, rounds, max_runtime: See ``build_request``.

    Returns:
        The request dict.
    """
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh preparation directory')
    inputs = validate_inputs(scored, snapshot)
    request = build_request(job_name, image_uri, inputs['rows'], rounds, max_runtime)
    (output / 'baseline').mkdir(parents=True)
    (output / 'manifest').mkdir()
    shutil.copyfile(scored, output / 'baseline/bq1_scored.parquet')
    shutil.copyfile(snapshot, output / 'manifest/dashboard.json')
    # model_promotion=False records that this job can only produce a candidate.
    provenance = {**inputs, 'job_name': job_name, 'image_uri': image_uri,
                  'status': 'prepared-not-submitted', 'model_promotion': False}
    (output / 'manifest/input.json').write_text(json.dumps(provenance, indent=2), encoding='utf-8')
    (output / 'request.json').write_text(json.dumps(request, indent=2), encoding='utf-8')
    return request


def validate_bundle(directory):
    """Re-check a prepared bundle before it is submitted, inspected or collected.

    Because time may pass (and files may be edited) between ``prepare`` and
    ``start``, this recomputes everything: the copied inputs must still pass
    ``validate_inputs`` and match ``input.json``, and ``request.json`` must be
    *identical* to a freshly built request from the same parameters. Any hand
    edit (e.g. a bigger instance type or a different role) is therefore
    rejected.

    Args:
        directory: A folder created by ``prepare_bundle``.

    Returns:
        The validated request dict.

    Raises:
        ValueError: on any mismatch.
    """
    directory = Path(directory)
    provenance = json.loads((directory / 'manifest/input.json').read_text(encoding='utf-8'))
    request = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
    inputs = validate_inputs(directory / 'baseline/bq1_scored.parquet',
                             directory / 'manifest/dashboard.json')
    if any(provenance.get(key) != value for key, value in inputs.items()):
        raise ValueError('Prepared input manifest mismatch')
    expected = build_request(provenance['job_name'], provenance['image_uri'], inputs['rows'],
                             int(request['HyperParameters']['rounds']),
                             request['StoppingCondition']['MaxRuntimeInSeconds'])
    if request != expected:
        raise ValueError('Training request differs from the bounded contract')
    return request


def train_channels(baseline, manifest, work, model_dir, report_dir, rounds=120, threads=4,
                   expected_rows=None):
    """SageMaker stages S3 files; training code needs no AWS credentials or network.

    Called by ``sagemaker/train.py`` with SageMaker's standard paths:
    ``baseline=/opt/ml/input/data/baseline``,
    ``manifest=/opt/ml/input/data/manifest``,
    ``work=/opt/ml/output/work/candidate``, ``model_dir=/opt/ml/model`` and
    ``report_dir=/opt/ml/output/data``. Hyperparameters come from
    ``/opt/ml/input/config/hyperparameters.json``.

    Steps:

    1. Re-verify the staged inputs against the reviewed ``input.json``.
    2. Re-check the bounds (rows, rounds 1-120, threads 1-4) in case the
       container is run with different hyperparameters.
    3. Run ``compare_models`` into ``work`` and add an ``execution`` section
       to the report (job name, image, input hashes, ``candidate-only``).
    4. Copy the artifacts into ``model_dir`` (SageMaker packs that folder into
       ``model.tar.gz`` in S3) and reload them with ``SavedRanker`` to prove
       the saved model is loadable and its hashes still match.
    5. Copy ``comparison.json`` into ``report_dir`` and print a one-line JSON
       status to the job log (CloudWatch).

    Args:
        baseline, manifest: Channel directories.
        work: Fresh scratch directory for ``compare_models``.
        model_dir: Output model directory (must be empty).
        report_dir: Output data directory for the report.
        rounds, threads: Training limits.
        expected_rows: Row count from the job's hyperparameters, or None.

    Returns:
        The final report dict.

    Raises:
        ValueError: on any verification or bounds failure. ``train.py`` then
            writes the message to ``/opt/ml/output/failure`` so it shows up as
            the job's FailureReason.
    """
    baseline, manifest = Path(baseline), Path(manifest)
    scored, snapshot = baseline / 'bq1_scored.parquet', manifest / 'dashboard.json'
    inputs = validate_inputs(scored, snapshot)
    provenance = json.loads((manifest / 'input.json').read_text(encoding='utf-8'))
    # The data SageMaker staged must be byte-identical to what was reviewed.
    if any(provenance.get(key) != value for key, value in inputs.items()):
        raise ValueError('Staged source differs from reviewed manifest')
    if expected_rows is not None and inputs['rows'] != expected_rows:
        raise ValueError('Job expected row count mismatch')
    if not 1 <= rounds <= 120 or not 1 <= threads <= 4:
        raise ValueError('Training exceeds the bounded contract')
    report = compare_models(scored, snapshot, work, rounds=rounds, threads=threads)
    report['execution'] = {'service': 'sagemaker-training-container',
                           'job_name': provenance['job_name'], 'image_uri': provenance['image_uri'],
                           **inputs, 'promotion': 'candidate-only'}
    # compare_models' generic limitation says "no AWS training", which is no
    # longer true here; replace it with the accurate candidate-only note.
    report['limitations'] = [item for item in report['limitations']
                            if item != 'No model promotion, AWS training, or dashboard replacement.']
    report['limitations'].append('Candidate only; no model promotion or dashboard replacement.')
    # Overwrite comparison.json with the enriched report. selection.json and
    # features.json are untouched, so their recorded hashes stay valid.
    (Path(work) / 'comparison.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    # SageMaker creates /opt/ml/model before entry; compare_models requires a fresh work directory.
    model_dir, report_dir = Path(model_dir), Path(report_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    if any(model_dir.iterdir()):
        raise ValueError('Refuse to overwrite an existing model artifact')
    # Copy every file produced (both candidate .ubj models, JSONs, queue CSVs).
    for path in Path(work).iterdir():
        if path.is_file():
            shutil.copyfile(path, model_dir / path.name)
    # Smoke test: raises if the copied artifacts fail their hash checks.
    SavedRanker(model_dir, threads=threads)
    shutil.copyfile(model_dir / 'comparison.json', report_dir / 'comparison.json')
    print(json.dumps({'status': 'candidate-trained', 'rows': report['total_rows'],
                      'validation_gate_passed': report['selection']['validation_gate_passed'],
                      'model_promoted': False}), flush=True)
    return report
