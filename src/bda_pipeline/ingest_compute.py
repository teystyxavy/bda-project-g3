"""Independent reference checks, rule scoring and release export run by Glue.

The numerical functions accept local input/output directories. glue_compute
stages S3 channels, runs these functions on the Glue driver, uploads successful
results and writes _SUCCESS last. They remain directly testable without AWS.
SageMaker is used only for optional candidate training.
"""
import gc
import hashlib
import json
import os
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from .acquisition import validate_fx
from .analytics import add_behaviour, ranking_metrics
from .batch import (COLUMNS, FEATURES, RECEIVER_FEATURES, SENDER_FEATURES, daily_row_counts, evaluation_periods,
                    export_verified_batch)
from .cloud_scores import checked_indices, feature_fingerprints
from .dashboard import records
from .inference import SCORING_COLUMNS, verify_artifacts
from .model import INPUT_COLUMNS, SavedRanker, digest, rank
from .network import COLUMNS as NETWORK_COLUMNS, analyse_network, check_shape, compare_network, dashboard_section, read_tables
from .release import prepare_release
from .splits import run_splits
from .transform import TRANSACTION_COLUMNS, IDENTIFIER_DTYPES


def raw_fingerprints(frame):
    """Hash each transaction's original source fields into a 32-byte fingerprint.

    The 11 source columns are normalised to canonical types first - timestamp
    as integer nanoseconds, amounts as float64, the label as int64, identifiers
    as strings - so the same transaction gives the same hash whether it came
    from the raw CSV or from the curated Parquet. Each row is serialised as a
    compact JSON array and hashed with SHA-256.

    Args:
        frame: DataFrame containing ``TRANSACTION_COLUMNS``.

    Returns:
        NumPy array of dtype ``'S32'`` (raw 32-byte digests), one per row, in
        the frame's row order.

    Raises:
        ValueError: if any source field is missing (NaN).
    """
    normalized = frame[TRANSACTION_COLUMNS].copy()
    # Timestamps -> int nanoseconds since epoch: unambiguous and JSON-serialisable.
    normalized['timestamp'] = pd.to_datetime(normalized.timestamp).map(lambda t: pd.Timestamp(t).value)
    for name in ['amount_paid', 'amount_received']:
        normalized[name] = pd.to_numeric(normalized[name], errors='raise').astype('float64')
    normalized['is_laundering'] = pd.to_numeric(normalized.is_laundering, errors='raise').astype('int64')
    if normalized.isna().any().any():
        raise ValueError('Missing source field')
    # .digest() (raw bytes, 32) instead of .hexdigest() (64 chars) halves memory
    # for ~5 million rows.
    return np.asarray([hashlib.sha256(json.dumps(row, separators=(',', ':'), allow_nan=False).encode()).digest()
                       for row in normalized.itertuples(index=False, name=None)], dtype='S32')


def verify_behaviour(sender_cohort, receiver_cohort):
    """Recomputes the behaviour features of each cohort's complete history with ``analytics.add_behaviour`` and requires Glue's values to match exactly.
    The sender cohort proves ``SENDER_FEATURES`` (outgoing and directed-pair) and the receiver cohort ``RECEIVER_FEATURES`` (incoming), because each side
    only holds the complete history of its own accounts. Each argument is a list of frames; an empty list is skipped.
    Returns the number of reference rows; raises AssertionError on the first mismatch."""
    checked_rows = 0
    for cohort, features in [(sender_cohort, SENDER_FEATURES), (receiver_cohort, RECEIVER_FEATURES)]:
        if cohort:
            # Original file order, because the windows depend on chronological order.
            reference = pd.concat(cohort, ignore_index=True).sort_values('source_row_index').reset_index(drop=True)
            actual = reference[features].copy()
            recalculated = add_behaviour(reference)
            np.testing.assert_array_equal(actual.to_numpy(), recalculated[features].to_numpy())
            checked_rows += len(reference)
    return checked_rows


def verify_curated(curated, raw, manifest):
    """Independently prove that the Glue-curated dataset matches the raw inputs.

    Checks performed:

    1. **Inputs unchanged** - each staged raw file's SHA-256 and size equal the
       manifest values.
    2. **FX table valid** - ``validate_fx`` over [first day - 1, last day].
    3. **Census** - the raw CSV is streamed in 100k-row chunks and fingerprinted
       (``raw_fingerprints``); it must have exactly ``manifest['rows']`` rows.
    4. **Row identity** - every curated row is matched to its raw row via
       ``source_row_index`` (each index exactly once, via ``checked_indices``)
       and its fingerprint must equal the raw fingerprint, i.e. curation did
       not alter any original field, drop or duplicate any row.
    5. **Partitioning** - each row's ``txn_date`` partition equals its timestamp's date.
    6. **Account join** - sender and receiver entity IDs are present.
    7. **Values** - ``amount_usd``, ``amount_usd_asof`` and the behaviour
       features are finite and non-negative.
    8. **USD conversion** - ``amount_usd = amount_paid x rate(same day)`` and
       ``amount_usd_asof = amount_paid x rate(previous day)``, to a relative
       tolerance of 1e-12.
    9. **Labels** - the number of laundering labels equals the raw count.
    10. **Behaviour features** - for deterministic ~1% samples of senders and
        of receivers (chosen by hashing bank+account, *not* by label), the full
        history is collected and recomputed with ``analytics.add_behaviour``.
        The sender sample checks the outgoing and directed-pair features and the
        receiver sample the incoming ones (``verify_behaviour``); each must match
        Glue's values exactly.

    Args:
        curated: Directory of hive-partitioned curated Parquet (``txn_date=...``).
        raw: Directory with ``transactions.csv``, ``accounts.csv``, ``fx.csv``.
        manifest: Validated run manifest.

    Returns:
        A proof dict (``status='passed'``, row/positive counts, total USD, a
        per-``txn_date`` breakdown of the same (``daily``) and a description
        of what was compared). Written to ``proof.json`` by the caller.

    Raises:
        ValueError / AssertionError: on the first mismatch.
    """
    raw = Path(raw)
    # 1. Raw input integrity.
    for name, info in manifest['files'].items():
        if digest(raw / name) != info['sha256'] or (raw / name).stat().st_size != info['bytes']:
            raise ValueError('Staged raw source hash/size mismatch: ' + name)
    # 2. FX validity. One extra day before start_date for the previous-day valuation.
    rates = pd.read_csv(raw / 'fx.csv')
    validate_fx(rates, pd.Timestamp(manifest['start_date']).date() - pd.Timedelta(days=1),
                pd.Timestamp(manifest['end_date']).date(), manifest['currencies'])
    # Lookup table: (date string, currency name) -> USD per unit.
    fx = {(str(day), str(currency)): float(value) for day, currency, value in
          rates[['txn_date', 'currency', 'usd_rate']].itertuples(index=False, name=None)}
    n = manifest['rows']
    # 3. Fingerprint every raw row; position i corresponds to source_row_index i.
    raw_hashes = np.empty(n, dtype='S32')
    offset = positives_raw = 0
    # Read identifiers as strings so leading zeros in bank/account IDs survive.
    dtype = {name: 'string' for name in TRANSACTION_COLUMNS if name not in ['amount_paid', 'amount_received', 'is_laundering']}
    # names=... renames the duplicate 'Account' headers positionally.
    for frame in pd.read_csv(raw / 'transactions.csv', header=0, names=TRANSACTION_COLUMNS,
                             dtype=dtype, chunksize=100_000):
        if offset + len(frame) > n:
            raise ValueError('Unexpected raw population')
        frame.timestamp = pd.to_datetime(frame.timestamp, format='%Y/%m/%d %H:%M', errors='raise')
        raw_hashes[offset:offset + len(frame)] = raw_fingerprints(frame)
        positives_raw += int(frame.is_laundering.sum())
        offset += len(frame)
    if offset != n:
        raise ValueError('Incomplete raw population')
    # 4-9. Walk the curated Parquet in batches and compare with the raw side.
    seen = np.zeros(n, dtype=bool)  # which source rows have been matched so far
    positives = 0
    amount_usd = 0.
    # Per-day breakdown (rows/positives/amount_usd), independently recomputed from the raw CSV here
    # alongside the whole-population totals above - lets export_verified_batch verify a date-subset
    # export against an independently-verified figure instead of trusting the curated file's own metadata.
    daily = {}
    histories, receiver_histories = [], []
    # exclude_invalid_files skips non-Parquet objects such as _SUCCESS.
    dataset = ds.dataset(curated, format='parquet', partitioning='hive', exclude_invalid_files=True)
    for batch in dataset.to_batches(batch_size=100_000):
        frame = batch.to_pandas()
        # Validates source_row_index (in range, unique, not seen before) and marks it seen.
        indices = checked_indices(frame, seen, manifest.get('subset', 'HI-Small'))
        if not np.array_equal(raw_hashes[indices], raw_fingerprints(frame)):
            raise ValueError('Curated transaction differs from original source')
        if not frame.txn_date.astype(str).eq(frame.timestamp.dt.strftime('%Y-%m-%d')).all():
            raise ValueError('Curated date partition mismatch')
        if frame[['from_entity_id', 'to_entity_id']].isna().any().any():
            raise ValueError('Incomplete account join')
        for column in ['amount_usd', 'amount_usd_asof', *FEATURES]:
            values = frame[column].to_numpy(dtype=float)
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError('Invalid causal feature or USD value')
        # shift 0 = same-day rate; shift 1 = previous day's rate (known at
        # transaction time, so no look-ahead for the model feature).
        for column, shift in [('amount_usd', 0), ('amount_usd_asof', 1)]:
            days = (frame.timestamp - pd.Timedelta(days=shift)).dt.strftime('%Y-%m-%d')
            expected = frame.amount_paid.to_numpy() * np.asarray([
                fx[(day, currency)] for day, currency in zip(days, frame.payment_currency)])
            np.testing.assert_allclose(frame[column], expected, rtol=1e-12, atol=1e-8)
        # Label-independent ~1% cohorts: senders (outgoing features) and receivers (incoming features).
        # Zero-stripped bank IDs; the first 8 hex digits of SHA-256(bank:account) mod 100 == 0 keeps ~1%.
        for cohort, side in [(histories, 'from'), (receiver_histories, 'to')]:
            bank = frame[f'{side}_bank'].astype(str).str.lstrip('0').replace('', '0')
            keep = pd.Series([int(hashlib.sha256(f'{b}:{a}'.encode()).hexdigest()[:8], 16) % 100 == 0
                              for b, a in zip(bank, frame[f'{side}_account'])], index=frame.index)
            if keep.any(): cohort.append(frame.loc[keep, COLUMNS])
        positives += int(frame.is_laundering.sum())
        amount_usd += float(frame.amount_usd.sum())
        for day, group in frame.groupby(frame.txn_date.astype(str)):
            entry = daily.setdefault(day, {'rows': 0, 'positives': 0, 'amount_usd': 0.})
            entry['rows'] += len(group)
            entry['positives'] += int(group.is_laundering.sum())
            entry['amount_usd'] += float(group.amount_usd.sum())
    # Every raw row must have appeared exactly once, with the same labels.
    if not seen.all() or positives != positives_raw:
        raise ValueError('Curated census or label count differs from source')
    # 10. Recompute behaviour features for each cohort's full history.
    checked_rows = verify_behaviour(histories, receiver_histories)
    return {'status': 'passed', 'rows': n, 'positives': positives, 'amount_usd': amount_usd, 'daily': daily,
            'subset': manifest.get('subset', 'HI-Small'), 'start_date': manifest['start_date'], 'end_date': manifest['end_date'],
            'raw_identity_comparison': 'Every source row: ordered SHA256 fingerprint',
            'fx_comparison': 'All same-day and prior-day valuations',
            'behaviour_reference_rows': checked_rows,
            'behaviour_scope': 'Complete histories for deterministic label-independent approximately 1% sender and receiver cohorts'}


def baseline_stage(inputs, output, assets):
    """Glue stage 'baseline': verify curation, build the rule baseline.

    Reads (under ``inputs``): ``run/manifest.json``, ``curated/`` Parquet,
    ``raw/`` CSVs and ``model/deployment.json``.

    Writes (under ``output``, uploaded to ``pipeline/runs/<run_id>/baseline/``):

    * ``proof.json`` - result of ``verify_curated`` (the control Lambda checks
      ``status == 'passed'`` before continuing);
    * ``baseline/`` - from ``batch.export_verified_batch``: the rule-based BQ1
      risk scores (``bq1_scored.parquet``) and a dashboard snapshot;
    * ``manifest/dashboard.json`` - copy of that snapshot;
    * ``manifest/input.json`` - provenance for the optional SageMaker training
      job (hashes of the scored file and snapshot, expected job name and image).
      ``status='prepared-not-submitted'`` and ``model_promotion=False`` record
      that nothing is trained or promoted by this stage itself.

    Args:
        inputs: ``the Glue scratch input directory`` (or a local test folder).
        output: ``the Glue scratch output directory``.
        assets: Folder with the static dashboard files (``/app/dashboard``).

    Returns:
        The curation proof dict.
    """
    inputs, output = Path(inputs), Path(output)
    manifest = json.loads((inputs / 'run/manifest.json').read_text())
    output.mkdir(parents=True, exist_ok=True)
    proof = verify_curated(inputs / 'curated', inputs / 'raw', manifest)
    proof_path = output / 'proof.json'
    proof_path.write_text(json.dumps(proof, allow_nan=False))
    # export_dates restricts baseline/export/training to a chosen set of txn_date partitions out of
    # an otherwise fully-curated run (curate itself always processes the full population). Absent,
    # behaviour is unchanged from before this field existed.
    export_dates = manifest.get('export_dates')
    dates = export_dates['dates'] if export_dates else None
    counts = daily_row_counts(ds.dataset(inputs / 'curated', format='parquet', partitioning='hive'), dates) if manifest['train'] else None
    # Scoring only proceeds if proof.json says 'passed' (checked inside export_verified_batch).
    export_verified_batch(inputs / 'curated', proof_path, output / 'baseline', assets, serving_only=True, subset=manifest.get('subset', 'HI-Small'), end_date=manifest['end_date'],
                         splits=run_splits(manifest, inputs / 'model', counts), dates=dates)
    # Label where the snapshot was produced, and record the tail boundary (if any) for compare_models'
    # dense-vs-tail reporting, for the dashboard's provenance panel.
    snapshot = output / 'baseline/dashboard/dashboard.json'
    data = json.loads(snapshot.read_text())
    data['provenance']['scoring_location'] = 'aws-glue rule baseline'
    if export_dates:
        data['provenance']['tail_start'] = export_dates.get('tail_start')
    snapshot.write_text(json.dumps(data, allow_nan=False))
    (output / 'manifest').mkdir()
    shutil.copyfile(snapshot, output / 'manifest/dashboard.json')
    # Provenance consumed by the training job; it ties the training input to these exact files.
    provenance = {'rows': manifest['rows'], 'scored_sha256': digest(output / 'baseline/bq1_scored.parquet'),
                  'snapshot_sha256': digest(snapshot), 'job_name': 'is459-g3-train-' + manifest['run_id'],
                  'image_uri': json.loads((inputs / 'model/deployment.json').read_text())['image_uri'],
                  'status': 'prepared-not-submitted', 'model_promotion': False}
    (output / 'manifest/input.json').write_text(json.dumps(provenance))
    return proof


def verify_scores(reference, scored, model_dir, manifest, output):
    """Recompute every Glue XGBoost score with the frozen model and compare.

    Two passes:

    1. Over the baseline reference Parquet (verified in the baseline stage):
       record a feature fingerprint per ``source_row_index`` and check every
       categorical value is in the model's vocabulary.
    2. Over the Glue ``scored`` output: require exactly the scoring columns
       plus ``model_score`` and ``txn_date`` (in particular *no*
       ``is_laundering`` label, so the scoring job could not have seen labels);
       check each row's features are identical to the reference; check scores
       are finite and in [0, 1]; and re-predict with ``SavedRanker`` - Glue's
       score must match within rtol 1e-6 / atol 1e-7 (allowing tiny
       floating-point differences between machines).

    Args:
        reference: Path to the baseline ``bq1_scored.parquet``.
        scored: Directory of Glue scored Parquet (hive-partitioned).
        model_dir: Frozen model artefacts directory.
        manifest: Run manifest (row count, expected model manifest hash).
        output: Folder to write ``scores.npy`` into.

    Returns:
        ``(scores, proof)`` - a float64 array indexed by ``source_row_index``,
        and a proof dict with the largest observed absolute difference.

    Raises:
        ValueError / AssertionError: on any mismatch or missing row.
    """
    # Check the model files are exactly the reviewed ones before using them.
    verify_artifacts(model_dir, manifest['model_manifest_sha256'])
    from .export_workers import ScoreWorkers
    n = manifest.get('source_index_rows', manifest['rows'])
    selected_rows = manifest['rows']
    seen = np.zeros(n, dtype=bool)
    fingerprints = np.empty(n, dtype='S32')
    scores = np.empty(n, dtype='float64')
    difference = 0.
    worker_count = min(16, os.cpu_count() or 1, max(1, (selected_rows + 1_999_999) // 2_000_000))
    print(f'EXPORT_SCORE_WORKERS workers={worker_count}', flush=True)
    with ScoreWorkers(model_dir, workers=worker_count) as workers:
        batches = ds.dataset(reference, format='parquet').to_batches(columns=SCORING_COLUMNS, batch_size=100_000)
        processed = 0
        for frame, (values, _) in workers.batches(batches):
            indices = checked_indices(frame, seen, manifest.get('subset', 'HI-Small'))
            fingerprints[indices] = values
            processed += len(frame)
            if processed % 2_000_000 < len(frame):
                print(f'EXPORT_REFERENCE_SCORES rows={processed}/{selected_rows}', flush=True)
        if int(seen.sum()) != selected_rows:
            raise ValueError('Incomplete baseline score reference')
        reference_seen = seen.copy()
        seen[:] = False
        batches = ds.dataset(scored, format='parquet', partitioning='hive', exclude_invalid_files=True).to_batches(batch_size=100_000)
        processed = 0
        for frame, (values, delta) in workers.batches(batches, predict=True):
            if set(frame.columns) != set(SCORING_COLUMNS) | {'model_score', 'txn_date'}:
                raise ValueError('Unexpected scoring columns or evaluation labels')
            indices = checked_indices(frame, seen, manifest.get('subset', 'HI-Small'))
            if not np.array_equal(fingerprints[indices], values):
                raise ValueError('Scored feature mismatch')
            scores[indices] = frame.model_score.to_numpy(dtype='float64')
            difference = max(difference, delta)
            processed += len(frame)
            if processed % 2_000_000 < len(frame):
                print(f'EXPORT_CLOUD_SCORES rows={processed}/{selected_rows}', flush=True)

    if not np.array_equal(seen, reference_seen): raise ValueError('Incomplete cloud scores')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    np.save(output / 'scores.npy', scores, allow_pickle=False)
    return scores, {'status': 'passed', 'rows': selected_rows, 'max_absolute_score_difference': difference,
                    'labels_excluded': True, 'model_sha256': SavedRanker(model_dir).selection['selected']['sha256']}


def export_stage(inputs, output, assets, checkpoint_factory=None, component_review=False):
    """Glue stage 'export': verify scores + mule network, build the release.

    Reads (under ``inputs``): ``run/manifest.json``; ``baseline/baseline/
    bq1_scored.parquet`` and ``baseline/manifest/dashboard.json`` (from the
    baseline stage); ``scored/`` (Glue scoring); ``model/`` (frozen model and
    ``deployment.json``); ``network/`` (the Glue network tables); and, if
    ``manifest['train']``, ``candidate/model.tar.gz`` from SageMaker training.

    Steps:

    1. ``verify_scores`` - all model scores reproduced (BQ1).
    2. Network reconciliation (BQ2): recompute hubs, loops and rings in pandas
       (``network.analyse_network``) and compare with every Glue network
       table (``network.compare_network``), then refuse implausible results
       (``network.check_shape``); the dashboard's ``network`` section is
       built from the verified result.
    3. For each evaluation period in ``snapshot['splits']`` (every window after
       train): ranking metrics (precision/recall/lift at K=100, average
       precision) and the top-2000 explained review queue for the dashboard.
    4. Add model, pipeline and provenance metadata to the snapshot.
    5. If training was requested: safely unpack the candidate archive, check
       its provenance (same rows, job name, image, input hash, candidate-only)
       and recompute its reported metrics; the candidate is *never* promoted.
    6. Enrich the snapshot, write ``dashboard/`` (snapshot + static assets)
       and package it with ``release.prepare_release`` into ``release/``,
       whose ``manifest.json`` lists file hashes and the verification proofs.

    The control Lambda's ``publish`` op checks this release manifest before
    copying files to the public dashboard bucket.

    Args:
        inputs: ``the Glue scratch input directory``.
        output: ``the Glue scratch output directory`` (uploaded to ``.../export/``).
        assets: Static dashboard files folder (``/app/dashboard``).

    Returns:
        The release manifest dict.
    """
    inputs, output = Path(inputs), Path(output)
    manifest = json.loads((inputs / 'run/manifest.json').read_text())
    output.mkdir(parents=True, exist_ok=True)
    # export/release only ever cover the full population (release.py requires aml_scope=='full');
    # a date-subset run is a training-candidate exploration and has no public release to build.
    # Skip explicitly here rather than letting verify_scores/release raise partway through, so the
    # Step Functions execution reports this stage as a clean, intentional no-op, not a failure.
    if manifest.get('export_dates'):
        result = {'status': 'skipped', 'reason': 'date-subset run has no public release'}
        (output / 'skipped.json').write_text(json.dumps(result))
        return result
    reference = inputs / 'baseline/baseline/bq1_scored.parquet'
    print('EXPORT_PHASE reference_hash', flush=True)
    reference_hash = digest(reference)
    print('EXPORT_PHASE verify_scores', flush=True)
    # ---- 1. BQ1: model scores ----
    scores, score_proof = verify_scores(reference, inputs / 'scored', inputs / 'model', manifest, output / 'evidence')
    # ---- 2. BQ2: Glue network tables vs independent pandas recomputation ----
    if not (inputs / 'network/_SUCCESS').exists(): raise ValueError('Require the complete Glue network output')
    dataset = ds.dataset(reference, format='parquet')

    network_dataset = dataset
    if manifest.get('selection', {}).get('network_scope') == 'full-source':
        network_dataset = ds.dataset(inputs / 'network_reference', format='parquet', partitioning='hive')

    def network_batches():
        # Two passes (links, then candidate-loop payment times) over only the needed columns.
        for batch in network_dataset.to_batches(columns=NETWORK_COLUMNS, batch_size=200_000):
            yield batch.to_pandas()
    print('EXPORT_PHASE verify_network', flush=True)
    checkpoint = None
    if checkpoint_factory:
        from importlib.resources import files
        checkpoint = checkpoint_factory({'schema': 1, 'reference_sha256': reference_hash,
            'manifest_sha256': digest(inputs / 'run/manifest.json'),
            'network_code_sha256': hashlib.sha256(files('bda_pipeline').joinpath('network.py').read_bytes()).hexdigest()})
    network_result = analyse_network(network_batches, checkpoint=checkpoint)
    print('EXPORT_PHASE network_recomputed', flush=True)
    network_proof = compare_network(network_result, read_tables(inputs / 'network'), manifest.get('network_rows', manifest['rows']))
    # Verify the full detection result first; oversized connected groups are
    # retained as private evidence, not presented as single investigation cases.
    network_review = None
    if component_review:
        from .network_review import investigation_view
        network_result, network_review, quarantined, shape = investigation_view(network_result)
        network_proof['shape'] = shape
        network_proof['component_review'] = network_review
        if network_review['components']:
            review_dir = output / 'evidence/oversized-networks'
            review_dir.mkdir(parents=True)
            for name, frame in quarantined.items():
                frame.to_parquet(review_dir / (name + '.parquet'), index=False)
            (review_dir / 'summary.json').write_text(json.dumps(network_review, allow_nan=False))
            print(f"EXPORT_NETWORK quarantined_components={network_review['components']} accounts={network_review['accounts']}", flush=True)
        del quarantined
    else:
        network_proof['shape'] = check_shape(network_result)
    (output / 'evidence/scores.json').write_text(json.dumps(score_proof, allow_nan=False))
    (output / 'evidence/network.json').write_text(json.dumps(network_proof, allow_nan=False))
    # ---- 3. Per-period metrics and review queues ----
    ranker = SavedRanker(inputs / 'model')
    snapshot = json.loads((inputs / 'baseline/manifest/dashboard.json').read_text())
    export_cache = None
    if checkpoint:
        from .export_checkpoint import ExportCheckpoint
        from .ingest_contract import sha
        # Bind every runtime module: a new numerical implementation never
        # silently reuses old dashboard calculations. Network migration remains
        # separate and still goes through full Spark parity and shape checks.
        package = files('bda_pipeline')
        code = {item.name: hashlib.sha256(item.read_bytes()).hexdigest()
                for item in package.iterdir() if item.name.endswith('.py')}
        export_cache = ExportCheckpoint(checkpoint.s3, manifest['run_id'], {
            'schema': 1, 'reference_sha256': reference_hash,
            'manifest_sha256': digest(inputs / 'run/manifest.json'),
            'baseline_dashboard_sha256': digest(inputs / 'baseline/manifest/dashboard.json'),
            'code_sha256': sha(code), 'network_proof_sha256': sha(network_proof),
            'model_sha256': ranker.selection['selected']['sha256']})

    def substage(name, function):
        return export_cache.compute(name, function) if export_cache else function()

    # Build the bounded network projection before scanning model views. Do not
    # retain millions of raw edges/payments throughout later dashboard scans.
    network_section = substage('network-dashboard', lambda: dashboard_section(network_result))
    del network_result
    gc.collect()
    views = {}
    from .dashboard_exploration import stream_model_view
    for name, (start, end) in evaluation_periods(snapshot['splits'], manifest['end_date']).items():
        print(f'EXPORT_PHASE model_view {name}', flush=True)
        views[name] = substage('model-view-' + name,
            lambda: stream_model_view(dataset, scores, ranker, start, end, manifest['subset']))
    # ---- 4. Metadata for the dashboard ----
    snapshot['model'] = {'name': ranker.selection['selected']['name'], 'views': views,
        'inference_source': 'aws-glue', 'validation_gate_passed': ranker.selection['validation_gate_passed'],
        'selection_policy': ranker.selection['selection_policy'],
        'score_definition': 'Uncalibrated ranking score; active frozen model, not a newly promoted candidate.',
        'explanation_definition': 'Signed TreeSHAP margins reconstructed in AWS processing; not causal evidence.',
        'provenance': {'model_sha256': ranker.selection['selected']['sha256'],
                       'source_sha256': reference_hash, 'training_source_sha256': ranker.selection['source_sha256']}}
    snapshot['pipeline'] = {key: manifest[key] for key in ['run_id', 'dataset_version', 'subset', 'source_scope',
                             'start_date', 'end_date', 'processing_sha256', 'deployment_sha256', 'train']}
    # Only a model frozen on a different subset is a transfer evaluation (HI-Small is the original model's own subset).
    training_subset = ranker.report.get('training_subset', snapshot.get('provenance', {}).get('model_training_subset', 'HI-Small'))
    snapshot['model']['training_subset'] = training_subset
    snapshot['model']['training_window'] = ranker.report.get('splits', snapshot['splits'])['train']
    development_dates = ranker.report.get('development_dates', manifest.get('selection', {}).get('development_dates'))
    if development_dates:
        from .dashboard_exploration import evaluation_date_breakdown
        test_start, test_end = evaluation_periods(snapshot['splits'], manifest['end_date'])['test']
        snapshot['model']['test_date_breakdown'] = substage('test-date-breakdown',
            lambda: evaluation_date_breakdown(dataset, scores, development_dates, test_start, test_end, manifest['subset']))
    transfer = not manifest['train'] and manifest['subset'] != training_subset
    snapshot['model']['evaluation_scope'] = (
        f"Transfer evaluation on {manifest['subset']} with the frozen {training_subset} model; synthetic populations may overlap, so this is not an independent generalisation claim."
        if transfer else f"{manifest['subset']} chronological evaluation")
    snapshot['pipeline']['fx_sha256'] = manifest['files']['fx.csv']['sha256']
    snapshot['pipeline']['refresh_time'] = datetime.now(timezone.utc).isoformat()
    # ---- 5. Optional: verify the candidate model from SageMaker training ----
    training_proof = None
    if manifest['train']:
        archives = list((inputs / 'candidate').rglob('model.tar.gz'))
        if len(archives) != 1: raise ValueError('Require one candidate archive')
        candidate = output / 'candidate'
        candidate.mkdir()
        with tarfile.open(archives[0], 'r:gz') as archive:
            members = archive.getmembers()
            # Guard against "zip bombs": at most 30 files / 200 MB uncompressed.
            if len(members) > 30 or sum(item.size for item in members) > 200_000_000:
                raise ValueError('Oversized candidate archive')
            names = set()
            for member in members:
                # The archive's own root folder entry is harmless; skip it.
                if member.isdir() and member.name in ['.', './']: continue
                name = member.name.removeprefix('./')
                # Only flat regular files with expected extensions: blocks path
                # traversal ('../x'), sub-folders, symlinks and duplicates.
                if (not member.isfile() or '/' in name or '\\' in name or name in names
                        or name in ['', '.', '..'] or not name.endswith(('.json', '.ubj', '.csv'))):
                    raise ValueError('Unsafe candidate archive')
                names.add(name)
                # Copy file contents manually instead of extractall(), so the
                # archive can never choose where files are written.
                with archive.extractfile(member) as source, (candidate / name).open('wb') as target:
                    shutil.copyfileobj(source, target)
        candidate_ranker = SavedRanker(candidate)
        report = json.loads((candidate / 'comparison.json').read_text())
        execution = report['execution']
        image = json.loads((inputs / 'model/deployment.json').read_text())['image_uri']
        # The candidate must have been trained by this run's job, with the
        # deployed image, on exactly this run's baseline data, and marked as
        # candidate-only (not promoted).
        if (report['total_rows'] != manifest['rows'] or execution['job_name'] != 'is459-g3-train-' + manifest['run_id']
                or execution['image_uri'] != image or execution['scored_sha256'] != reference_hash
                or candidate_ranker.selection['source_sha256'] != reference_hash
                or execution['promotion'] != 'candidate-only'):
            raise ValueError('Candidate provenance mismatch')
        # Recompute the candidate's and the rule baseline's metrics per period
        # and require them to equal what the training job reported.
        for name, (start, end) in evaluation_periods(snapshot['splits'], manifest['end_date']).items():
            frame = dataset.to_table(columns=INPUT_COLUMNS, filter=(ds.field('timestamp') >= pd.Timestamp(start)) & (ds.field('timestamp') < pd.Timestamp(end))).to_pandas()
            values = candidate_ranker.predict(frame)
            metrics_frame = frame[['timestamp', 'source_row_index', 'is_laundering']]
            for kind, metrics in [('model', ranking_metrics(rank(metrics_frame, values))),
                                  ('baseline', ranking_metrics(rank(metrics_frame, frame.risk_score.to_numpy())))]:
                reviewed = report['metrics'][name][kind]
                if set(metrics) != set(reviewed): raise ValueError('Candidate metric schema mismatch')
                for key, actual in metrics.items():
                    # None means "undefined" (e.g. no positives in the period); both must agree.
                    if actual is None:
                        if reviewed[key] is not None: raise ValueError('Candidate metric mismatch')
                    elif not np.isclose(actual, reviewed[key], rtol=1e-9, atol=1e-12):
                        raise ValueError('Candidate metric mismatch')
            del frame, values
            gc.collect()
        training_proof = {'status': 'passed', 'candidate_only': True, 'model_promoted': False,
            'archive_sha256': digest(archives[0]), 'validation_gate_passed': candidate_ranker.selection['validation_gate_passed']}
        snapshot['pipeline']['training'] = training_proof
    # ---- 6. Build the dashboard site and the release package ----
    snapshot['provenance']['scoring_location'] = 'AWS Glue scoring and mule-network analysis, Glue export'
    # BQ2 is the verified mule-network section; the retired treasury views are removed.
    snapshot['network'] = network_section
    snapshot['network']['verification'] = network_proof
    if network_review:
        snapshot['network']['component_review'] = network_review
        if network_review['components']:
            snapshot['network']['definitions']['ring'] += ' ' + network_review['reason']
    if manifest.get('selection'):
        snapshot['network']['population'] = {'kind': 'full-source', 'subset': manifest['subset'],
            'rows': manifest['network_rows'], 'start_date': manifest['selection']['network_start_date'],
            'end_date': manifest['selection']['network_end_date']}
    for key in ['treasury', 'treasury_daily']:
        snapshot.pop(key, None)
    snapshot['buildStatus'] = 'complete'
    snapshot['generated_at'] = snapshot['pipeline']['refresh_time']
    # Imported lazily, only at this point of the export stage.
    from .dashboard_exploration import enrich_snapshot
    print('EXPORT_PHASE exploration', flush=True)
    def build_exploration():
        enrich_snapshot(snapshot, dataset, scores, ranker, treasury=False)
        return snapshot['exploration']
    snapshot['exploration'] = substage('exploration', build_exploration)
    site = output / 'dashboard'
    site.mkdir()
    (site / 'dashboard.json').write_text(
        json.dumps(snapshot, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    for name in ['index.html', 'dashboard.js', 'dashboard.css']: shutil.copyfile(Path(assets) / name, site / name)
    # prepare_release copies the four runtime files and screens them for
    # secrets / row-level labels; the manifest records each file's hash.
    release = prepare_release(site, output / 'release')
    release['pipeline_run_id'] = manifest['run_id']
    release['processing_sha256'] = manifest['processing_sha256']
    release['verification'] = {'scores': score_proof, 'network': network_proof}
    if training_proof: release['verification']['training'] = training_proof
    (output / 'release/manifest.json').write_text(json.dumps(release, allow_nan=False))
    return release
