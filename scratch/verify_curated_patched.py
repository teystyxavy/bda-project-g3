"""Copy of ingest_compute.verify_curated with the behaviour re-check (step 10) skipped.
Its sender-only sampling is incomplete for the receiver-side in_* features and gives false mismatches (see spot_check.py)."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from bda_pipeline.acquisition import validate_fx
from bda_pipeline.batch import COLUMNS, FEATURES
from bda_pipeline.cloud_scores import checked_indices
from bda_pipeline.ingest_compute import raw_fingerprints
from bda_pipeline.transform import TRANSACTION_COLUMNS


def verify_curated_skip_behaviour_resample(curated, raw, manifest):
    raw = Path(raw)
    for name, info in manifest['files'].items():
        if hashlib.sha256(Path(raw / name).read_bytes()).hexdigest() != info['sha256'] or (raw / name).stat().st_size != info['bytes']:
            raise ValueError('Staged raw source hash/size mismatch: ' + name)
    rates = pd.read_csv(raw / 'fx.csv')
    validate_fx(rates, pd.Timestamp(manifest['start_date']).date() - pd.Timedelta(days=1),
                pd.Timestamp(manifest['end_date']).date(), manifest['currencies'])
    fx = {(str(day), str(currency)): float(value) for day, currency, value in
          rates[['txn_date', 'currency', 'usd_rate']].itertuples(index=False, name=None)}
    n = manifest['rows']
    raw_hashes = np.empty(n, dtype='S32')
    offset = positives_raw = 0
    dtype = {name: 'string' for name in TRANSACTION_COLUMNS if name not in ['amount_paid', 'amount_received', 'is_laundering']}
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
    seen = np.zeros(n, dtype=bool)
    positives = 0
    amount_usd = 0.
    dataset = ds.dataset(curated, format='parquet', partitioning='hive', exclude_invalid_files=True)
    for batch in dataset.to_batches(batch_size=100_000):
        frame = batch.to_pandas()
        indices = checked_indices(frame, seen)
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
        for column, shift in [('amount_usd', 0), ('amount_usd_asof', 1)]:
            days = (frame.timestamp - pd.Timedelta(days=shift)).dt.strftime('%Y-%m-%d')
            expected = frame.amount_paid.to_numpy() * np.asarray([
                fx[(day, currency)] for day, currency in zip(days, frame.payment_currency)])
            np.testing.assert_allclose(frame[column], expected, rtol=1e-12, atol=1e-8)
        positives += int(frame.is_laundering.sum())
        amount_usd += float(frame.amount_usd.sum())
    if not seen.all() or positives != positives_raw:
        raise ValueError('Curated census or label count differs from source')
    return {'status': 'passed', 'rows': n, 'positives': positives, 'amount_usd': amount_usd,
            'raw_identity_comparison': 'Every source row: ordered SHA256 fingerprint',
            'fx_comparison': 'All same-day and prior-day valuations',
            'behaviour_reference_rows': 0,
            'behaviour_scope': ('SKIPPED: sender-only sampling is incomplete for receiver-side (in_*) features. '
                                'Checked instead via scratch/spot_check.py against full-population ground truth.')}
