"""Column contracts, evaluation periods and training-only thresholds for batch scoring.

Shared by the full-population export (``control.export``), the model
(``modeling.model``), the dashboard builders and ``control.ingest_compute``.
"""
import numpy as np

from ..features.behaviour import FEATURES


# Columns read from the curated Parquet. Reading a fixed column list keeps the
# batches small and fails fast (see the "missing" check below) if the Glue job
# output schema ever drifts.
COLUMNS = ['source_row_index', 'transaction_id', 'timestamp', 'txn_date',
           'from_bank', 'from_account', 'to_bank', 'to_account', 'payment_format',
           'payment_currency', 'receiving_currency', 'amount_usd', 'amount_usd_asof',
           'is_laundering', *FEATURES]

# Columns shown to a compliance analyst in the BQ1 review queue. The label
# (is_laundering) is deliberately NOT included: the queue is what a reviewer
# would see in production, where the true label is unknown.
QUEUE_COLUMNS = ['transaction_id', 'timestamp', 'from_bank', 'from_account',
                 'to_bank', 'to_account', 'payment_format', 'payment_currency',
                 'amount_usd_asof', 'risk_score', 'risk_reasons', 'txn_count_1h',
                 'distinct_counterparties_24h']

# Chronological evaluation windows as half-open [start, end) date ranges.
# Training is 2022-09-01 .. 2022-09-06 (see fit_thresholds); validation and
# test are the next two-day blocks; "tail" is the sparse late period of the
# dataset, reported separately because it has very few transactions.
PERIODS = {'validation': ('2022-09-07', '2022-09-09'),
           'test': ('2022-09-09', '2022-09-11'), 'tail': ('2022-09-11', '2022-09-19')}


def fit_thresholds(dataset):
    """Exact pandas P99 convention, using training amounts only.

    Learns the "unusually large amount" rule: for each payment format (ACH,
    Wire, Cheque, ...) the 99th percentile of ``amount_usd_asof`` over the
    training window 2022-09-01 (inclusive) to 2022-09-07 (exclusive).

    Args:
        dataset: pyarrow Dataset over the curated Parquet.

    Returns:
        dict mapping payment_format -> P99 USD threshold (float).

    Why it is done this way:
        * Only training rows are used, so validation/test periods never
          influence the threshold (no look-ahead leakage).
        * All training amounts per format are collected and passed to one
          ``np.quantile`` call (linear interpolation, the same default as
          pandas ``Series.quantile``), so the result is exact rather than an
          approximate streaming quantile. Only three columns are read to keep
          memory low.

    Raises:
        ValueError: if the training window contains no rows.
    """
    amounts = {}
    for batch in dataset.to_batches(columns=['timestamp', 'payment_format', 'amount_usd_asof'], batch_size=100_000):
        frame = batch.to_pandas()
        training = frame.loc[frame.timestamp.ge('2022-09-01') & frame.timestamp.lt('2022-09-07')]
        for name, group in training.groupby('payment_format'):
            amounts.setdefault(name, []).append(group.amount_usd_asof.to_numpy())
    if not amounts:
        raise ValueError('No training observations')
    return {name: float(np.quantile(np.concatenate(values), .99)) for name, values in amounts.items()}
