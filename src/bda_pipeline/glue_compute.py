"""S3 staging and commit boundary for Glue baseline and dashboard export jobs.

The numerical reference functions remain independent of Spark curation. They
run on the Glue driver; executors are not used for these Python calculations.
No outputs are uploaded until calculations pass; _SUCCESS is uploaded last.
"""
import json
import shutil
from pathlib import Path, PurePosixPath

from .ingest_contract import BUCKET, paths, sha, stage_request, validate_manifest


def safe_relative(key):
    path = PurePosixPath(key)
    if not key or path.is_absolute() or '\\' in key or any(p in ('..', '.') for p in key.split('/')):
        raise ValueError('Unsafe S3 input path')
    return Path(*path.parts)


def download_input(s3, key, target):
    """Download an exact object or a paginated prefix without path traversal."""
    safe_relative(key.rstrip('/'))
    if key.endswith('/'):
        objects = (item for page in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=key)
                   for item in page.get('Contents', []) if not item['Key'].endswith('/'))
    else:
        objects = [{'Key': key}]
    count = 0
    for item in objects:
        name = item['Key']
        if not name.startswith(key):
            raise ValueError('Input escaped its prefix')
        relative = name[len(key):] if key.endswith('/') else PurePosixPath(key).name
        destination = target / safe_relative(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        size = item.get('Size')
        if size is None:
            size = s3.head_object(Bucket=BUCKET, Key=name)['ContentLength']
        if shutil.disk_usage(target).free < size + 1024 ** 3:
            raise ValueError('Insufficient Glue scratch disk; no output committed')
        s3.download_file(BUCKET, name, str(destination))
        count += 1
    if not count:
        raise ValueError('Missing Glue stage input')


def run_stage(s3, run_id, stage, sources, deployment_hash, output_uri, assets, workspace):
    """Stage verified inputs, compute, upload outputs, then commit the stage.

    Source channels must exactly match the request rebuilt from the deployment
    and run manifest. Fresh output prefixes prevent overwriting partial runs.
    A failed upload leaves no _SUCCESS and cannot be published.
    """
    p = paths(run_id)
    if stage not in ('baseline', 'export') or output_uri != f's3://{BUCKET}/{p[stage]}/':
        raise ValueError('Unexpected stage output')
    prefix = p[stage] + '/'
    if s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1).get('KeyCount', 0):
        raise ValueError('Stage output already exists; use a fresh run')
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    inputs, output = workspace / 'input', workspace / 'output'
    # Read only the expected run manifest before trusting any supplied channel.
    download_input(s3, p['raw'] + '/manifest.json', inputs / 'run')
    manifest = json.loads((inputs / 'run/manifest.json').read_text())
    validate_manifest(manifest, deployment_hash)
    if manifest['run_id'] != run_id:
        raise ValueError('Manifest run mismatch')
    model_key = sources.get('model', '')
    if not model_key.startswith('pipeline/deployments/') or not model_key.endswith('/model/'):
        raise ValueError('Unexpected model deployment input')
    download_input(s3, model_key + 'deployment.json', inputs / 'model')
    config = json.loads((inputs / 'model/deployment.json').read_text())
    if sha(config) != deployment_hash or manifest['model_manifest_sha256'] != config['model_manifest_sha256']:
        raise ValueError('Deployment identity mismatch')
    extra = {k: v for k, v in sources.items() if k in ('athena', 'candidate')}
    expected = stage_request(run_id, stage, config, extra, manifest['subset'], rows=manifest['rows'])
    if sources != json.loads(expected['Arguments']['--INPUTS_JSON']):
        raise ValueError('Unexpected Glue input channels')
    if stage == 'export' and ('athena' not in sources or ('candidate' in sources) != manifest['train']):
        raise ValueError('Missing export verification input')
    local_paths = {'baseline': 'baseline/baseline', 'baseline_manifest': 'baseline/manifest'}
    for name, key in sources.items():
        if name != 'run':
            download_input(s3, key, inputs / local_paths.get(name, name))
    from .ingest_compute import baseline_stage, export_stage
    result = (baseline_stage if stage == 'baseline' else export_stage)(inputs, output, Path(assets))
    for file in sorted(output.rglob('*')):
        if file.is_file():
            s3.upload_file(str(file), BUCKET, prefix + file.relative_to(output).as_posix(),
                           ExtraArgs={'ServerSideEncryption': 'AES256'})
    # Glue 5 ships boto3 1.34.131, before conditional PutObject parameters.
    # The pipeline lock, job max_concurrent_runs=1 and fresh-prefix check above
    # provide exclusive ownership; commit only after every upload succeeds.
    s3.put_object(Bucket=BUCKET, Key=prefix + '_SUCCESS', Body=b'', ServerSideEncryption='AES256')
    return result
