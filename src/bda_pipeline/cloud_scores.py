"""Reconcile frozen full-population cloud scores before a local dashboard export."""
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
    """SHA256 of typed, ordered feature values; independent of Parquet row order."""
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
    return np.asarray([hashlib.sha256(json.dumps(row, ensure_ascii=False, separators=(',', ':'),
                    allow_nan=False).encode('utf-8')).digest()
                    for row in normalized.itertuples(index=False, name=None)], dtype='S32')


def checked_indices(frame, seen):
    raw = frame.source_row_index.to_numpy()
    indices = raw.astype('int64')
    if not np.array_equal(raw, indices) or ((indices < 0) | (indices >= len(seen))).any():
        raise ValueError('Source index outside expected integer range')
    if len(np.unique(indices)) != len(indices) or seen[indices].any():
        raise ValueError('Duplicate source index')
    if not frame.transaction_id.eq('HI-Small:' + frame.source_row_index.astype(str)).all():
        raise ValueError('Transaction identity mismatch')
    seen[indices] = True
    return indices


def verify_full_scores(curated, reference, model_dir, run_report, job_report, marker_report, output,
                       expected_rows=5_078_345, batch_size=100_000):
    """All-row feature/score parity, with bounded batches and index-addressed caches."""
    output, curated = Path(output), Path(curated)
    if output.exists():
        raise ValueError('Use a fresh verification directory')
    run = json.loads(Path(run_report).read_text(encoding='utf-8-sig'))['JobRun']
    job = json.loads(Path(job_report).read_text(encoding='utf-8-sig'))['Job']
    receipt = json.loads(Path(marker_report).read_text(encoding='utf-8-sig'))
    marker = receipt['metadata']
    if run['JobName'] != 'is459-g3-score-frozen' or run['JobRunState'] != 'SUCCEEDED':
        raise ValueError('Require a successful frozen-scoring job')
    args = job['NonOverridableArguments']
    location = urlsplit(args['--OUTPUT'])
    if (job['Name'] != run['JobName'] or int(args['--EXPECTED_ROWS']) != expected_rows
            or args.get('--conf') != 'spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs=true'
            or location.scheme != 's3' or not location.netloc
            or receipt.get('job_run_id') != run['Id'] or receipt.get('bucket') != location.netloc
            or receipt.get('key') != location.path.strip('/')+'/_SUCCESS'
            or args['--MANIFEST_SHA256'] != digest(Path(model_dir)/'model-manifest.json')):
        raise ValueError('Full job configuration/success-marker receipt mismatch')
    if marker.get('ContentLength') != 0 or marker.get('ServerSideEncryption') != 'AES256':
        raise ValueError('Require the verified zero-byte encrypted success marker')
    if expected_rows <= 0 or batch_size <= 0:
        raise ValueError('Positive row and batch bounds required')
    verify_artifacts(model_dir, args['--MANIFEST_SHA256'])
    ranker = SavedRanker(model_dir)
    source_hash = digest(reference)
    if ranker.selection['source_sha256'] != source_hash or ranker.report['total_rows'] != expected_rows:
        raise ValueError('Frozen model/reference population mismatch')
    fingerprints = np.empty(expected_rows, dtype='S32')
    seen = np.zeros(expected_rows, dtype=bool)
    for batch in ds.dataset(reference, format='parquet').to_batches(columns=SCORING_COLUMNS, batch_size=batch_size):
        frame = batch.to_pandas()
        indices = checked_indices(frame, seen)
        fingerprints[indices] = feature_fingerprints(frame)
    if not seen.all():
        raise ValueError('Incomplete native reference')
    dataset = ds.dataset(curated, format='parquet', partitioning='hive')
    if set(dataset.schema.names) != set(SCORING_COLUMNS) | {'model_score', 'txn_date'}:
        raise ValueError('Unexpected cloud columns or evaluation labels')
    paths = sorted(curated.rglob('*.parquet'))
    if not paths:
        raise ValueError('No cloud output Parquet')
    before = {str(p.relative_to(curated)): digest(p) for p in paths}
    scores = np.empty(expected_rows, dtype='float64')
    seen[:] = False
    max_difference = 0.0
    dates = set()
    for batch in dataset.to_batches(batch_size=batch_size):
        frame = batch.to_pandas()
        indices = checked_indices(frame, seen)
        if not np.array_equal(fingerprints[indices], feature_fingerprints(frame)):
            raise ValueError('Cloud feature fingerprint differs from frozen reference')
        actual = frame.model_score.to_numpy(dtype='float64')
        if not np.isfinite(actual).all() or ((actual < 0) | (actual > 1)).any():
            raise ValueError('Invalid cloud score')
        native = ranker.predict(frame[SCORING_COLUMNS])
        np.testing.assert_allclose(actual, native, rtol=1e-6, atol=1e-7)
        max_difference = max(max_difference, float(np.abs(actual-native).max()))
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
    path = Path(report_path)
    report = json.loads(path.read_text(encoding='utf-8'))
    if (report.get('status') != 'passed' or report.get('scope') != 'full-population-cloud-scoring'
            or report.get('job_state') != 'SUCCEEDED' or report.get('success_marker_verified') is not True
            or report.get('rows') != expected_rows or report.get('source_sha256') != source_hash
            or report.get('model_sha256') != model_hash):
        raise ValueError('Full cloud verification/model/source mismatch')
    cache_info = report['score_cache']
    if (not cache_info['file'] or Path(cache_info['file']).name != cache_info['file']
            or cache_info['file'] in {'.', '..'}):
        raise ValueError('Score cache must be a sibling file')
    cache = path.parent / cache_info['file']
    if cache.is_symlink() or digest(cache) != cache_info['sha256']:
        raise ValueError('Verified score cache hash mismatch')
    values = np.load(cache, mmap_mode='r', allow_pickle=False)
    if values.shape != (expected_rows,) or values.dtype != np.dtype('float64'):
        raise ValueError('Score cache shape/type mismatch')
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError('Invalid cached cloud scores')
    return values, report
