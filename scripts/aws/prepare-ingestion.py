"""Prepare immutable ingestion artifacts and Terraform variables; no AWS calls.

Where this sits in the pipeline
-------------------------------
Deployment step 1 of the batch ingestion stack (``infra/ingestion``: control
Lambda, Glue curate/score jobs, SageMaker processing role, Step Functions
``is459-g3-ingest``). It gathers everything that stack uploads to S3 into one
fresh local folder, pins it by content hash, and writes the Terraform
variables. ``deploy-ingestion.ps1`` (first deployment) or
``update-ingestion.ps1`` (later updates) then verify the hashes and apply.

Produces in ``--output``
------------------------
* ``bda_pipeline.zip`` - all of ``src/bda_pipeline/*.py`` for the Glue jobs.
* ``control.zip`` - only the modules the control Lambda needs
  (``ingest_contract``, ``ingest_control``, ``stage_cache``, ``release``).
* ``curate_transactions.py`` / ``score_transactions.py`` / ``network_mules.py`` - Glue job scripts
  (the network job is BQ2 and is never stage-cached: it always reruns, so a
  new deployment can never serve old BQ2 output).
* ``runtime.gluewheels.zip`` - copied from ``--wheels`` (see
  ``bundle-scoring-wheels.py``).
* ``model/`` - the frozen XGBoost model and its JSON companions, after
  ``verify_artifacts`` confirms every file matches ``model-manifest.json``.
* ``deployment.json`` (also copied into ``model/``) - the pipeline config later
  stored at ``s3://<BUCKET>/pipeline/deployment.json``. Every run manifest
  records this file's hash, so runs are tied to one exact deployment.
* ``terraform.tfvars.json`` - input variables for ``infra/ingestion``.
* ``artifact-hashes.json`` - SHA-256 of every file above, for tamper checks.

Content addressing: the S3 prefix ``pipeline/deployments/<sha>`` is the hash
of all artifact hashes plus the image URI, so a new build never overwrites an
old one (old runs stay reproducible) and identical builds map to one prefix.

Usage
-----
    python scripts/aws/prepare-ingestion.py --output output/optimisation-artifacts-YYYYMMDD \
        --models output/models --wheels output/runtime.gluewheels.zip \
        --image-uri 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@sha256:<digest>
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from pathlib import Path

# Repo root; src/ is added to sys.path so the script runs without PYTHONPATH.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from bda_pipeline.ingest_contract import ACCOUNT, REGION, BUCKET, sha
from bda_pipeline.inference import verify_artifacts


def digest(path):
    """Return the SHA-256 hex digest of a file (streamed, so large files are fine).
    """
    with Path(path).open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def zip_sources(target, names, minimal=False):
    """Write selected ``src/bda_pipeline`` modules into a reproducible zip.

    Every entry gets the same fixed timestamp (2026-01-01) and is added in
    sorted order, so zipping identical sources always produces byte-identical
    zips - and therefore identical hashes and an unchanged deployment prefix.

    Args:
        target: zip file path to create.
        names: module file names (e.g. ``'ingest_control.py'``) to include under
            ``bda_pipeline/`` inside the zip.
        minimal: if True, replace ``__init__.py`` with a one-line docstring so
            the Lambda package does not import modules it does not ship.
    """
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(names):
            info = zipfile.ZipInfo('bda_pipeline/' + name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            data = b'"""Ingestion control package."""\n' if minimal and name == '__init__.py' else (ROOT / 'src/bda_pipeline' / name).read_bytes()
            archive.writestr(info, data)


def prepare(output, models, wheels, image_uri):
    """Build the artifact folder, deployment config and Terraform variables.

    Args:
        output: fresh folder to create (must not exist).
        models: folder holding the trained model and ``model-manifest.json``.
        wheels: path to the ``.gluewheels.zip`` bundle.
        image_uri: ECR image *pinned by digest* (``...@sha256:<64 hex>``), so
            SageMaker always runs exactly the reviewed image, never a moved tag.

    Returns:
        The deployment config dict (also written as ``deployment.json``).

    Raises:
        ValueError: output exists, image URI not pinned to the project ECR repo,
            model artifact hash mismatch, or an unsafe model file name.
    """
    if output.exists(): raise ValueError('Use a fresh preparation directory')
    # Only accept <account>.dkr.ecr.<region>.amazonaws.com/is459-g3-training@sha256:...
    if not re.fullmatch(f'{ACCOUNT}\\.dkr\\.ecr\\.{REGION}\\.amazonaws\\.com/is459-g3-training@sha256:[0-9a-f]{{64}}', image_uri):
        raise ValueError('Require the pinned Group 3 ECR image digest')
    # Hash the manifest, then check every model file against it (allowlisted
    # names, no symlinks, matching hashes) before anything is copied.
    model_hash = digest(models / 'model-manifest.json')
    manifest = verify_artifacts(models, model_hash)
    output.mkdir(parents=True)
    # No dataset, state, credentials, or unrelated workspace files in these packages.
    zip_sources(output / 'bda_pipeline.zip', [p.name for p in (ROOT / 'src/bda_pipeline').glob('*.py')])
    # The control Lambda only needs the contract, controller, stage cache and
    # release helpers - no pandas/XGBoost code.
    zip_sources(output / 'control.zip', ['__init__.py', 'ingest_contract.py', 'ingest_control.py', 'stage_cache.py', 'release.py'], minimal=True)
    for name in ['curate_transactions.py', 'score_transactions.py', 'network_mules.py']:
        shutil.copyfile(ROOT / 'jobs' / name, output / name)
    shutil.copyfile(wheels, output / 'runtime.gluewheels.zip')
    (output / 'model').mkdir()
    # Model files to deploy: the four JSON companions plus whichever model
    # file selection.json names as the selected candidate.
    files = ['model-manifest.json', 'comparison.json', 'features.json', 'selection.json']
    selection = json.loads((models / 'selection.json').read_text())
    files.append(selection['selected']['model_file'])
    for name in files:
        # Reject names with directory parts (e.g. '../x') - path traversal guard.
        if Path(name).name != name: raise ValueError('Unsafe model file')
        shutil.copyfile(models / name, output / 'model' / name)
    # Content-addressed S3 prefix: changes whenever any artifact or the image does.
    hashes = {p.relative_to(output).as_posix(): digest(p) for p in output.rglob('*') if p.is_file()}
    prefix = 'pipeline/deployments/' + sha({'files': hashes, 'image_uri': image_uri})
    # Deployment config read by ingest.py, the control Lambda and processing
    # jobs: where artifacts live, which model/image to trust, the IAM role
    # SageMaker processing jobs assume, and the CloudFront distribution/URL of
    # the dashboard that the publish step updates.
    config = {'schema_version': 1, 'artifact_prefix': prefix, 'model_prefix': prefix + '/model',
        'model_manifest_sha256': model_hash, 'image_uri': image_uri,
        'processing_role_arn': f'arn:aws:iam::{ACCOUNT}:role/is459-g3-ingest-processing',
        'distribution_id': 'EU1WB6XM4GKHQ', 'dashboard_url': 'https://d1n61tlw9pcrog.cloudfront.net/'}
    # Stage versions feed the stage cache key (bda_pipeline.stage_cache): if
    # the curate/score/network code, package, wheels or Glue runtime change, the
    # version changes and earlier cached Glue outputs are no longer reused.
    # The network job is self-contained (its thresholds are constants in the
    # script), so its own code hash is its whole version.
    config['stage_versions'] = {
        'curated': sha({'code': hashes['curate_transactions.py'], 'schema': 1, 'runtime': 'glue-5.0-python3.11'}),
        'scored': sha({'code': hashes['score_transactions.py'], 'package': hashes['bda_pipeline.zip'],
                       'wheels': hashes['runtime.gluewheels.zip'], 'runtime': 'glue-5.0-python3.11'}),
        'network': sha({'code': hashes['network_mules.py'], 'runtime': 'glue-5.0-python3.11'})}
    (output / 'model/deployment.json').write_text(json.dumps(config, indent=2))
    (output / 'deployment.json').write_text(json.dumps(config, indent=2))
    # Terraform reads artifacts from artifact_directory (forward slashes so the
    # Windows path works in HCL) and uploads them under artifact_prefix.
    variables = {'artifact_directory': str(output.resolve()).replace('\\', '/'), 'pipeline_config': config,
                 'model_file': files[-1]}
    (output / 'terraform.tfvars.json').write_text(json.dumps(variables, indent=2))
    # Written last and excluding itself: the deploy scripts re-hash every file
    # against this list before applying, to detect edits after preparation.
    (output / 'artifact-hashes.json').write_text(json.dumps({p.relative_to(output).as_posix(): digest(p)
        for p in output.rglob('*') if p.is_file() and p.name != 'artifact-hashes.json'}, indent=2))
    print(json.dumps({'status': 'prepared-not-deployed', 'deployment_sha256': sha(config), 'artifact_prefix': prefix}))
    return config


# CLI entry point; all four options are required.
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--wheels', type=Path, required=True)
    parser.add_argument('--image-uri', required=True)
    args = parser.parse_args()
    prepare(args.output, args.models, args.wheels, args.image_uri)
