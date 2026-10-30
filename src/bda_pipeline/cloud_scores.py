"""Reconcile frozen full-population cloud scores before a local dashboard export.

Where this sits in the pipeline
-------------------------------
The trained XGBoost ranker is "frozen" (its files are fixed and listed with
SHA-256 hashes in ``model-manifest.json``). The AWS Glue job
``is459-g3-score-frozen`` (``jobs/score_transactions.py``) then applies that
frozen model to every curated transaction and writes Parquet with an extra
``model_score`` column to S3, partitioned by ``txn_date``, plus a zero-byte
``_SUCCESS`` marker.

Before those cloud scores are trusted on the dashboard, this module proves
that the cloud job:

* ran the expected job configuration and finished successfully,
* scored exactly the same rows with exactly the same feature values as the
  local "native" reference Parquet the model was trained on, and
* produced the same scores the frozen model gives when run locally
  (within a tiny floating-point tolerance).

Functions
    ``verify_full_scores``  run by ``scripts/validation/verify-cloud-scoring-full.py``
                            on a downloaded copy of the Glue output plus JSON
                            saved from ``glue get-job-run``, ``glue get-job``
                            and an S3 head-object receipt for ``_SUCCESS``.
    ``load_verified_scores`` used by ``model_dashboard.export_model_dashboard``
                            to load the verified score cache.
    ``checked_indices`` / ``feature_fingerprints`` are also reused by
                            ``ingest_compute`` inside SageMaker Processing.

Reads only local files; makes no AWS calls itself.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from .inference import IDENTIFIERS, SCORING_COLUMNS, verify_artifacts
from .model import SavedRanker, digest


def feature_fingerprints(frame):
    """SHA256 of typed, ordered feature values; independent of Parquet row order.

    Args:
        frame: DataFrame containing all ``SCORING_COLUMNS``.

    Returns:
        numpy array of 32-byte SHA-256 digests, one per row, in the same order
        as ``frame``. The caller stores them by ``source_row_index`` so two
        datasets can be compared row by row even when Spark wrote rows in a
        different order or across different files.

    How values are normalised before hashing (so equal data hashes equally
    whether it came from pandas or from Spark):
        * identifier and category columns must already be Python strings;
        * timestamps become integer nanoseconds since the epoch;
        * ``source_row_index`` becomes int64;
        * every other (numeric) column becomes float64 and must be finite.

    Raises:
        ValueError: on a non-string identifier, a missing value or a
        non-finite number.
    """
    # Numeric columns = scoring columns minus identifiers, timestamp,
    # categories and the row index (those are handled separately above).
    numbers = [c for c in SCORING_COLUMNS if c not in IDENTIFIERS +
               ['timestamp', 'payment_format', 'payment_currency', 'receiving_currency', 'source_row_index']]
    normalized = frame[SCORING_COLUMNS].copy()
    for column in IDENTIFIERS + ['payment_format', 'payment_currency', 'receiving_currency']:
        if not normalized[column].map(lambda v: isinstance(v, str)).all():
            raise ValueError(f'Expected string: {column}')
    if normalized.isna().any().any():
        raise ValueError('Missing scoring feature')
    normalized['timestamp'] = normalized.timestamp.map(lambda value: pd.Timestamp(value).value)
    normalized['source_row_index'] = normalized.source_row_index.astype('int64')
    for column in numbers:
        normalized[column] = normalized[column].astype('float64')
        if not np.isfinite(normalized[column]).all():
            raise ValueError('Nonfinite scoring feature')
    # Compact JSON of the row tuple (fixed column order, no spaces) gives a
    # canonical byte string to hash.
    return np.asarray([hashlib.sha256(json.dumps(row, ensure_ascii=False, separators=(',', ':'),
                    allow_nan=False).encode('utf-8')).digest()
                    for row in normalized.itertuples(index=False, name=None)], dtype='S32')


def checked_indices(frame, seen, subset='HI-Small'):
    """Validate a batch's row identities and mark them as seen.

    Args:
        frame: batch with ``source_row_index`` and ``transaction_id``.
        seen: boolean numpy array, one slot per expected source row. It is
            updated in place so later batches cannot reuse the same rows.

    Returns:
        int64 numpy array of the batch's source row indices, used by the
        caller to address index-based caches (fingerprints, scores).

    Raises:
        ValueError: if an index is not a whole number, is out of range, is
        repeated (in this batch or an earlier one), or if ``transaction_id``
        is not ``'HI-Small:<source_row_index>'``.
    """
    raw = frame.source_row_index.to_numpy()
    indices = raw.astype('int64')
    # array_equal catches float indices like 3.5 that astype would truncate.
    if not np.array_equal(raw, indices) or ((indices < 0) | (indices >= len(seen))).any():
        raise ValueError('Source index outside expected integer range')
    if len(np.unique(indices)) != len(indices) or seen[indices].any():
        raise ValueError('Duplicate source index')
    if not frame.transaction_id.eq(subset + ':' + frame.source_row_index.astype(str)).all():
        raise ValueError('Transaction identity mismatch')
    seen[indices] = True
    return indices


def verify_full_scores(curated, reference, model_dir, run_report, job_report, marker_report, output,
                       expected_rows=5_078_345, batch_size=100_000):
    """All-row feature/score parity, with bounded batches and index-addressed caches.

    Args:
        curated: local copy of the Glue scoring output (hive-partitioned
            Parquet with ``model_score`` and ``txn_date``).
        reference: the local native Parquet the frozen model was built from
            (its SHA-256 must equal ``selection.json``'s ``source_sha256``).
        model_dir: frozen model directory with ``model-manifest.json``.
        run_report: JSON from ``aws glue get-job-run`` (has ``JobRun``).
        job_report: JSON from ``aws glue get-job`` (has ``Job``).
        marker_report: receipt for the S3 ``_SUCCESS`` object (bucket, key,
            job_run_id and head-object ``metadata``).
        output: new directory for ``verification.json`` and the score cache.
        expected_rows: population size; 5,078,345 is the HI-Small row count.
        batch_size: rows per Arrow batch (memory bound).

    Returns:
        The verification report dict (also written to
        ``output/verification.json``).

    Side effects:
        Writes ``scores-by-source-index.npy`` (float64 array where position
        i holds the cloud score of source row i) and ``verification.json``.

    Two passes are made, each in bounded batches:
        1. Reference pass: fingerprint every reference row by source index.
        2. Cloud pass: check each cloud row's fingerprint against pass 1,
           check the score is in [0, 1], re-score locally with the frozen
           model and require agreement within rtol=1e-6 / atol=1e-7.
    """
    output, curated = Path(output), Path(curated)
    if output.exists():
        raise ValueError('Use a fresh verification directory')
    # 'utf-8-sig' tolerates a byte-order mark, which PowerShell often adds
    # when saving AWS CLI output to a file.
    run = json.loads(Path(run_report).read_text(encoding='utf-8-sig'))['JobRun']
    job = json.loads(Path(job_report).read_text(encoding='utf-8-sig'))['Job']
    receipt = json.loads(Path(marker_report).read_text(encoding='utf-8-sig'))
    marker = receipt['metadata']
    if run['JobName'] != 'is459-g3-score-frozen' or run['JobRunState'] != 'SUCCEEDED':
        raise ValueError('Require a successful frozen-scoring job')
    # NonOverridableArguments are fixed in the Glue job definition (Terraform),
    # so a single run cannot quietly point the job at a different input,
    # output or model.
    args = job['NonOverridableArguments']
    location = urlsplit(args['--OUTPUT'])
    # Cross-check: job/run names match, row count, Spark is configured to
    # write a _SUCCESS marker, output is an s3:// URI, the marker receipt
    # belongs to this run and sits at <output>/_SUCCESS, and the job was
    # pinned to this exact model manifest hash.
    if (job['Name'] != run['JobName'] or int(args['--EXPECTED_ROWS']) != expected_rows
            or args.get('--conf') != 'spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs=true'
            or location.scheme != 's3' or not location.netloc
            or receipt.get('job_run_id') != run['Id'] or receipt.get('bucket') != location.netloc
            or receipt.get('key') != location.path.strip('/')+'/_SUCCESS'
            or args['--MANIFEST_SHA256'] != digest(Path(model_dir)/'model-manifest.json')):
        raise ValueError('Full job configuration/success-marker receipt mismatch')
    # The success marker is empty and encrypted at rest with SSE-S3 (AES256).
    if marker.get('ContentLength') != 0 or marker.get('ServerSideEncryption') != 'AES256':
        raise ValueError('Require the verified zero-byte encrypted success marker')
    if expected_rows <= 0 or batch_size <= 0:
        raise ValueError('Positive row and batch bounds required')
    # Re-hash every model artifact against the manifest before loading it.
    verify_artifacts(model_dir, args['--MANIFEST_SHA256'])
    ranker = SavedRanker(model_dir)
    source_hash = digest(reference)
    if ranker.selection['source_sha256'] != source_hash or ranker.report['total_rows'] != expected_rows:
        raise ValueError('Frozen model/reference population mismatch')
    # Pass 1: fingerprint the reference. Arrays are indexed by source row
    # (5M x 32 bytes, about 160 MB), so no join or sort is needed later.
    fingerprints = np.empty(expected_rows, dtype='S32')
    seen = np.zeros(expected_rows, dtype=bool)
    for batch in ds.dataset(reference, format='parquet').to_batches(columns=SCORING_COLUMNS, batch_size=batch_size):
        frame = batch.to_pandas()
        indices = checked_indices(frame, seen)
        fingerprints[indices] = feature_fingerprints(frame)
    if not seen.all():
        raise ValueError('Incomplete native reference')
    dataset = ds.dataset(curated, format='parquet', partitioning='hive')
    # The cloud output must contain exactly the scoring columns + score +
    # partition column. In particular it must NOT contain is_laundering:
    # labels never leave the training/evaluation side.
    if set(dataset.schema.names) != set(SCORING_COLUMNS) | {'model_score', 'txn_date'}:
        raise ValueError('Unexpected cloud columns or evaluation labels')
    paths = sorted(curated.rglob('*.parquet'))
    if not paths:
        raise ValueError('No cloud output Parquet')
    # Hash every output file now and again at the end, to prove the files
    # did not change while they were being checked.
    before = {str(p.relative_to(curated)): digest(p) for p in paths}
    scores = np.empty(expected_rows, dtype='float64')
    seen[:] = False
    max_difference = 0.0
    dates = set()
    # Pass 2: check the cloud output batch by batch.
    for batch in dataset.to_batches(batch_size=batch_size):
        frame = batch.to_pandas()
        indices = checked_indices(frame, seen)
        if not np.array_equal(fingerprints[indices], feature_fingerprints(frame)):
            raise ValueError('Cloud feature fingerprint differs from frozen reference')
        actual = frame.model_score.to_numpy(dtype='float64')
        if not np.isfinite(actual).all() or ((actual < 0) | (actual > 1)).any():
            raise ValueError('Invalid cloud score')
        # Re-score locally with the same frozen model; any real difference
        # (wrong model, wrong encoding) would be far above this tolerance.
        native = ranker.predict(frame[SCORING_COLUMNS])
        np.testing.assert_allclose(actual, native, rtol=1e-6, atol=1e-7)
        max_difference = max(max_difference, float(np.abs(actual-native).max()))
        # Each row must sit in the txn_date partition of its own timestamp.
        if not frame.txn_date.astype(str).eq(frame.timestamp.dt.strftime('%Y-%m-%d')).all():
            raise ValueError('Cloud date partition mismatch')
        dates.update(frame.txn_date.astype(str))
        scores[indices] = actual
        print(f'Checked {int(seen.sum()):,}/{expected_rows:,} cloud scores', flush=True)
    if not seen.all():
        raise ValueError('Incomplete cloud population')
    if before != {str(p.relative_to(curated)): digest(p) for p in sorted(curated.rglob('*.parquet'))}:
        raise ValueError('Cloud files changed during verification')
    if digest(reference) != source_hash:
        raise ValueError('Native reference changed during verification')
    output.mkdir(parents=True)
    # Save scores as a plain .npy (allow_pickle=False: no executable pickle
    # content) so the dashboard step can load them by source row index.
    cache = output / 'scores-by-source-index.npy'
    np.save(cache, scores, allow_pickle=False)
    report = dict(status='passed', scope='full-population-cloud-scoring', job_state='SUCCEEDED',
                  job_run_id=run['Id'], success_marker_verified=True, rows=expected_rows,
                  output_uri=args['--OUTPUT'], input_uri=args['--INPUT'],
                  source_sha256=source_hash, model_sha256=ranker.selection['selected']['sha256'],
                  feature_comparison='SHA256 of all ordered typed feature values for every source index',
                  max_absolute_score_difference=max_difference, score_rtol=1e-6, score_atol=1e-7,
                  date_partitions=sorted(dates), labels_excluded=True, output_parquet_sha256=before,
                  score_cache=dict(file=cache.name, sha256=digest(cache)),
                  explanation_scope='Dashboard explanations reconstructed locally with the frozen model')
    (output / 'verification.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    return report


def load_verified_scores(report_path, source_hash, model_hash, expected_rows):
    """Load the score cache written by ``verify_full_scores``, re-checking it first.

    Args:
        report_path: path to ``verification.json``.
        source_hash: SHA-256 of the native reference Parquet the caller uses.
        model_hash: SHA-256 of the selected frozen model file.
        expected_rows: population size the caller expects.

    Returns:
        (scores, report): a read-only memory-mapped float64 array where
        ``scores[i]`` is the verified cloud score of source row ``i``, and
        the parsed report dict.

    Raises:
        ValueError: if the report did not pass, belongs to a different
        source/model/population, the cache file is not a plain sibling file
        (blocks path tricks like ``../x`` or symlinks), its hash changed, or
        its shape, type or value range is wrong.
    """
    path = Path(report_path)
    report = json.loads(path.read_text(encoding='utf-8'))
    if (report.get('status') != 'passed' or report.get('scope') != 'full-population-cloud-scoring'
            or report.get('job_state') != 'SUCCEEDED' or report.get('success_marker_verified') is not True
            or report.get('rows') != expected_rows or report.get('source_sha256') != source_hash
            or report.get('model_sha256') != model_hash):
        raise ValueError('Full cloud verification/model/source mismatch')
    cache_info = report['score_cache']
    # The cache name must be a bare file name in the same folder as the report.
    if (not cache_info['file'] or Path(cache_info['file']).name != cache_info['file']
            or cache_info['file'] in {'.', '..'}):
        raise ValueError('Score cache must be a sibling file')
    cache = path.parent / cache_info['file']
    if cache.is_symlink() or digest(cache) != cache_info['sha256']:
        raise ValueError('Verified score cache hash mismatch')
    # mmap_mode='r' avoids reading all ~40 MB into memory at once and keeps
    # the array read-only.
    values = np.load(cache, mmap_mode='r', allow_pickle=False)
    if values.shape != (expected_rows,) or values.dtype != np.dtype('float64'):
        raise ValueError('Score cache shape/type mismatch')
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError('Invalid cached cloud scores')
    return values, report
