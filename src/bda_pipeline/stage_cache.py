"""Reuse fully verified stage objects; never skip independent run verification.

Purpose
-------
The two Glue stages (``curated`` = clean/join/USD-convert, ``scored`` = XGBoost
scoring) are the most expensive parts of a run. If a new run has exactly the
same inputs, dataset version, stage code versions and model as an earlier run
that was *fully verified and published*, this module copies that run's
curated/scored Parquet into the new run's prefix instead of re-running Glue.

Important: only the Glue compute is skipped. The new run still goes through
baseline verification (re-checking every curated row against the raw CSVs),
score verification and the release checks in ``ingest_compute``, so a bad cache
entry cannot be published unnoticed.

Where it sits
-------------
Called by the control Lambda (``ingest_control.handle``):

* ``op == 'begin'``   -> ``reuse()`` for 'curated' and 'scored'; the result
  (hit/miss) is saved to ``evidence/stage-cache.json`` and decides whether the
  Step Functions ``Curate`` / ``Score`` Glue tasks are skipped.
* ``op == 'publish'`` -> ``register()`` writes a cache receipt for this run's
  verified outputs, so later runs can reuse them.

S3 objects (all in ``BUCKET``):

* reads  ``pipeline/runs/<run>/{raw,curated,scored,baseline,evidence,export}/...``
* writes ``pipeline/stage-cache/<stage>/<key>.json`` (receipts, 30-day expiry)
* copies Parquet + ``_SUCCESS`` into ``pipeline/runs/<new run>/<stage>/``

The S3 helper functions (``get_json``/``put_json``) are passed in by the caller
so this module does not depend on the Lambda module (avoids a circular import).
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .ingest_contract import BUCKET, paths, sha, valid_run, validate_manifest


def stage_key(stage, manifest, config):
    """Compute the cache key (a SHA-256) that identifies a stage's output.

    Two runs get the same key only if everything that determines the stage
    output is identical: subset, row count, input file hashes, date range,
    Kaggle dataset version, the schema version and the deployed curated-stage
    code version. For ``scored`` the key additionally includes the scoring code
    version and the frozen model's manifest hash, since a different model would
    produce different scores.

    Args:
        stage: ``'curated'`` or ``'scored'``.
        manifest: Validated run manifest.
        config: Deployed configuration; ``config['stage_versions']`` maps stage
            name -> version string.

    Returns:
        The hex key, or ``None`` when caching is disabled for this stage (no
        version configured), in which case the caller always recomputes.

    Raises:
        ValueError: for an unsupported stage name.
    """
    if stage not in ['curated', 'scored']: raise ValueError('Unsupported cached stage')
    version = config.get('stage_versions', {}).get(stage)
    # No configured version = caching switched off for this stage.
    if version is None: return None
    identity = {key: manifest[key] for key in ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version']}
    # Scores are computed from curated data, so the curated version is part of
    # both keys: changing the curate job invalidates cached scores too.
    identity.update(schema_version=1, curated_version=config['stage_versions']['curated'])
    if stage == 'scored':
        identity.update(scored_version=version, model_manifest_sha256=manifest['model_manifest_sha256'])
    return sha(identity)


def inventory(s3, prefix):
    """List and sanity-check the objects that make up one stage output.

    Only Parquet data files and the Spark ``_SUCCESS`` marker count; anything
    else (e.g. Glue's ``_$folder$`` placeholders) is ignored. Limits (at most
    1,000 objects, each up to 4 GB) bound how much a cache copy can cost.

    Args:
        s3: boto3 S3 client.
        prefix: Stage prefix without trailing slash, e.g.
            ``pipeline/runs/<run>/curated``.

    Returns:
        A list of ``{'file': relative_key, 'bytes': size, 'etag': etag}`` sorted
        by file name, so two inventories can be compared with ``==``.

    Raises:
        ValueError: if limits are exceeded or the output is incomplete (no
        ``_SUCCESS`` marker or no Parquet file).
    """
    objects = []
    # Paginate because a listing returns at most 1,000 keys per page.
    for page in s3.get_paginator('list_objects_v2').paginate(Bucket=BUCKET, Prefix=prefix + '/'):
        for item in page.get('Contents', []):
            # Key relative to the stage folder, e.g. 'txn_date=2022-09-01/part-0.parquet'.
            relative = item['Key'][len(prefix) + 1:]
            if relative != '_SUCCESS' and not relative.endswith('.parquet'): continue
            if len(objects) >= 1000 or not 0 <= item['Size'] <= 4_000_000_000:
                raise ValueError('Stage cache exceeds the bounded object contract')
            # The ETag lets a later copy prove the object has not changed since.
            objects.append({'file': relative, 'bytes': item['Size'], 'etag': item['ETag']})
    # Spark writes _SUCCESS only after all part files are committed, so its
    # presence (plus at least one Parquet file) means the stage finished.
    if not any(item['file'] == '_SUCCESS' for item in objects) or not any(item['file'].endswith('.parquet') for item in objects):
        raise ValueError('Incomplete stage cache')
    return sorted(objects, key=lambda item: item['file'])


def register(s3, stage, manifest, config, get_json, put_json):
    """Record a verified stage output as reusable (write a cache receipt).

    Called at the end of a successful publish. The receipt stores the stage's
    object inventory plus hashes of the run's three proof documents
    (published result, release manifest, baseline proof). ``reuse()`` later
    re-reads those documents and compares hashes, so if anyone edits or deletes
    the source run's evidence, the cache entry stops being trusted.

    Args:
        s3: boto3 S3 client.
        stage: ``'curated'`` or ``'scored'``.
        manifest: The run's validated manifest.
        config: Deployed configuration.
        get_json: ``(s3, key) -> (dict | None, etag)`` reader.
        put_json: ``(s3, key, data)`` writer.

    Returns:
        None. Side effect: writes ``pipeline/stage-cache/<stage>/<key>.json``
        (overwriting any older receipt for the same key).

    Raises:
        ValueError: if the run is not published with a passed baseline proof,
        or its stage inventory is incomplete.
    """
    key = stage_key(stage, manifest, config)
    if key is None: return
    run_id = manifest['run_id']
    p = paths(run_id)
    result, _ = get_json(s3, p['evidence'] + '/result.json')
    release, _ = get_json(s3, p['export'] + '/release/manifest.json')
    baseline, _ = get_json(s3, p['baseline'] + '/proof.json')
    # Only cache outputs from runs that passed every verification and were published.
    if not result or result.get('status') != 'published' or baseline.get('status') != 'passed':
        raise ValueError('Cache registration requires a published verified run')
    objects = inventory(s3, p[stage])
    receipt = {'schema_version': 1, 'stage': stage, 'key': key, 'run_id': run_id,
               # Receipts expire after 30 days so stale outputs are not reused forever.
               'expires_at': (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
               'objects': objects, 'inventory_sha256': sha(objects),
               'result_sha256': sha(result), 'release_sha256': sha(release), 'baseline_sha256': sha(baseline)}
    put_json(s3, f'pipeline/stage-cache/{stage}/{key}.json', receipt)


def reuse(s3, stage, manifest, config, get_json):
    """Try to satisfy a stage from the cache by copying a verified earlier run.

    Steps:

    1. Compute the key; skip if ``--rerun`` was requested or caching is off.
    2. Load the receipt; a missing receipt is a normal cache miss.
    3. Validate the receipt strictly (schema, key, stage, not the same run,
       inventory hash). A malformed receipt *raises* (fail closed) rather than
       being treated as a miss, because it may indicate tampering.
    4. Expired receipt -> miss.
    5. Re-validate the source run's manifest and recompute its key.
    6. Re-hash the source run's proof documents and re-list its objects; any
       difference from the receipt raises.
    7. Copy each Parquet file with ``CopySourceIfMatch=<etag>`` (S3 refuses the
       copy if the object changed in between), 8 copies in parallel, and copy
       the ``_SUCCESS`` marker last.

    Args:
        s3: boto3 S3 client.
        stage: ``'curated'`` or ``'scored'``.
        manifest: The *new* run's validated manifest.
        config: Deployed configuration.
        get_json: ``(s3, key) -> (dict | None, etag)`` reader.

    Returns:
        ``{'hit': False, 'reason': 'bypassed'|'absent'|'expired'}`` on a miss, or
        ``{'hit': True, 'source_run_id', 'key', 'objects', 'bytes'}`` on a hit.

    Raises:
        ValueError: if a receipt exists but anything fails verification.
    """
    key = stage_key(stage, manifest, config)
    if manifest['rerun'] or key is None: return {'hit': False, 'reason': 'bypassed'}
    receipt, _ = get_json(s3, f'pipeline/stage-cache/{stage}/{key}.json')
    if receipt is None: return {'hit': False, 'reason': 'absent'}
    # Invalid receipts fail closed before creating any destination objects.
    source_run = valid_run(receipt['run_id'])
    # A run may not "reuse" itself, and the stored inventory must still hash
    # to the value recorded at registration time.
    if (receipt.get('schema_version') != 1 or receipt['key'] != key or receipt['stage'] != stage
            or source_run == manifest['run_id'] or sha(receipt['objects']) != receipt['inventory_sha256']):
        raise ValueError('Stage cache receipt mismatch')
    if datetime.fromisoformat(receipt['expires_at']) <= datetime.now(timezone.utc):
        return {'hit': False, 'reason': 'expired'}
    source = paths(source_run)
    # Re-check the source run's own manifest against its own deployment hash,
    # then confirm it really maps to the same cache key.
    original, _ = get_json(s3, source['raw'] + '/manifest.json')
    validate_manifest(original, original['deployment_sha256'])
    if stage_key(stage, original, config) != key: raise ValueError('Stage cache source mismatch')
    # The three proof documents must be byte-for-byte what was registered.
    for suffix, field in [('evidence/result.json', 'result_sha256'),
                          ('export/release/manifest.json', 'release_sha256'), ('baseline/proof.json', 'baseline_sha256')]:
        proof, _ = get_json(s3, f'pipeline/runs/{source_run}/{suffix}')
        if proof is None or sha(proof) != receipt[field]: raise ValueError('Stage cache verification receipt changed')
    # Same object names, sizes and ETags as at registration.
    if inventory(s3, source[stage]) != receipt['objects']: raise ValueError('Stage cache object inventory changed')
    destination = paths(manifest['run_id'])[stage]
    def copy(item):
        """Server-side copy one inventoried object into the new run's stage prefix.

        Before copying, ``head_object`` re-checks size, ETag and that the source
        is SSE-S3 (AES256) encrypted. ``CopySourceIfMatch`` makes S3 itself
        reject the copy if the object changed between the check and the copy.
        The copy happens inside S3, so no data flows through the Lambda.

        Args:
            item: One inventory entry ``{'file', 'bytes', 'etag'}``.

        Raises:
            ValueError: if the source object changed or is unencrypted.
        """
        source_key = source[stage] + '/' + item['file']
        head = s3.head_object(Bucket=BUCKET, Key=source_key)
        if (head['ContentLength'] != item['bytes'] or head['ETag'] != item['etag']
                or head.get('ServerSideEncryption') != 'AES256'):
            raise ValueError('Stage cache object changed or is unencrypted')
        s3.copy_object(Bucket=BUCKET, Key=destination + '/' + item['file'],
                       CopySource={'Bucket': BUCKET, 'Key': source_key},
                       CopySourceIfMatch=item['etag'], ServerSideEncryption='AES256')
    # Commit the success marker only after every data copy succeeds.
    # list(...) forces all copies to run and re-raises the first error.
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(copy, [item for item in receipt['objects'] if item['file'] != '_SUCCESS']))
    copy(next(item for item in receipt['objects'] if item['file'] == '_SUCCESS'))
    return {'hit': True, 'source_run_id': source_run, 'key': key,
            'objects': len(receipt['objects']), 'bytes': sum(item['bytes'] for item in receipt['objects'])}
