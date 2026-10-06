"""Reuse fully verified stage objects; never skip independent run verification."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .ingest_contract import BUCKET, paths, sha, valid_run, validate_manifest


def stage_key(stage, manifest, config):
    if stage not in ['curated', 'scored']: raise ValueError('Unsupported cached stage')
    version = config.get('stage_versions', {}).get(stage)
    if version is None: return None
    identity = {key: manifest[key] for key in ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version']}
    identity.update(schema_version=1, curated_version=config['stage_versions']['curated'])
    if stage == 'scored':
        identity.update(scored_version=version, model_manifest_sha256=manifest['model_manifest_sha256'])
    return sha(identity)


def inventory(s3, prefix):
    objects = []
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=prefix + '/'):
        for item in page.get('Contents', []):
            relative = item['Key'][len(prefix) + 1:]
            if relative != '_SUCCESS' and not relative.endswith('.parquet'): continue
            if len(objects) >= 250 or not 0 <= item['Size'] <= 4_000_000_000:
                raise ValueError('Stage cache exceeds the bounded object contract')
            objects.append({'file': relative, 'bytes': item['Size'], 'etag': item['ETag']})
    if not any(item['file'] == '_SUCCESS' for item in objects) or not any(item['file'].endswith('.parquet') for item in objects):
        raise ValueError('Incomplete stage cache')
    return sorted(objects, key=lambda item: item['file'])


def register(s3, stage, manifest, config, get_json, put_json):
    key = stage_key(stage, manifest, config)
    if key is None: return
    run_id = manifest['run_id']
    p = paths(run_id)
    result, _ = get_json(s3, p['evidence'] + '/result.json')
    release, _ = get_json(s3, p['export'] + '/release/manifest.json')
    baseline, _ = get_json(s3, p['baseline'] + '/proof.json')
    if not result or result.get('status') != 'published' or baseline.get('status') != 'passed':
        raise ValueError('Cache registration requires a published verified run')
    objects = inventory(s3, p[stage])
    receipt = {'schema_version': 1, 'stage': stage, 'key': key, 'run_id': run_id,
               'expires_at': (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
               'objects': objects, 'inventory_sha256': sha(objects),
               'result_sha256': sha(result), 'release_sha256': sha(release), 'baseline_sha256': sha(baseline)}
    put_json(s3, f'pipeline/stage-cache/{stage}/{key}.json', receipt)


def reuse(s3, stage, manifest, config, get_json):
    key = stage_key(stage, manifest, config)
    if manifest['rerun'] or key is None: return {'hit': False, 'reason': 'bypassed'}
    receipt, _ = get_json(s3, f'pipeline/stage-cache/{stage}/{key}.json')
    if receipt is None: return {'hit': False, 'reason': 'absent'}
    # Invalid receipts fail closed before creating any destination objects.
    source_run = valid_run(receipt['run_id'])
    if (receipt.get('schema_version') != 1 or receipt['key'] != key or receipt['stage'] != stage
            or source_run == manifest['run_id'] or sha(receipt['objects']) != receipt['inventory_sha256']):
        raise ValueError('Stage cache receipt mismatch')
    if datetime.fromisoformat(receipt['expires_at']) <= datetime.now(timezone.utc):
        return {'hit': False, 'reason': 'expired'}
    source = paths(source_run)
    original, _ = get_json(s3, source['raw'] + '/manifest.json')
    validate_manifest(original, original['deployment_sha256'])
    if stage_key(stage, original, config) != key: raise ValueError('Stage cache source mismatch')
    for suffix, field in [('evidence/result.json', 'result_sha256'),
                          ('export/release/manifest.json', 'release_sha256'), ('baseline/proof.json', 'baseline_sha256')]:
        proof, _ = get_json(s3, f'pipeline/runs/{source_run}/{suffix}')
        if proof is None or sha(proof) != receipt[field]: raise ValueError('Stage cache verification receipt changed')
    if inventory(s3, source[stage]) != receipt['objects']: raise ValueError('Stage cache object inventory changed')
    destination = paths(manifest['run_id'])[stage]
    def copy(item):
        source_key = source[stage] + '/' + item['file']
        head = s3.head_object(Bucket=BUCKET, Key=source_key)
        if (head['ContentLength'] != item['bytes'] or head['ETag'] != item['etag']
                or head.get('ServerSideEncryption') != 'AES256'):
            raise ValueError('Stage cache object changed or is unencrypted')
        s3.copy_object(Bucket=BUCKET, Key=destination + '/' + item['file'],
                       CopySource={'Bucket': BUCKET, 'Key': source_key},
                       CopySourceIfMatch=item['etag'], ServerSideEncryption='AES256')
    # Commit the success marker only after every data copy succeeds.
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(copy, [item for item in receipt['objects'] if item['file'] != '_SUCCESS']))
    copy(next(item for item in receipt['objects'] if item['file'] == '_SUCCESS'))
    return {'hit': True, 'source_run_id': source_run, 'key': key,
            'objects': len(receipt['objects']), 'bytes': sum(item['bytes'] for item in receipt['objects'])}
