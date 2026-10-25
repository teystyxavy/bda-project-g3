"""Transparent rule-based AML risk score (the BQ1 baseline) and its evaluation.

``score_baseline`` applies the rules (ACH + large amount + behaviour) and
attaches human-readable reasons; ``evaluate_baseline`` fits the amount
thresholds on the training period and reports ``ranking_metrics`` per later
period. Labels are only read for evaluation.

Chronological splits used throughout the project (timestamps, end exclusive):
train 2022-09-01..09-07, validation 09-07..09-09, test 09-09..09-11, and a
sparse late "tail" 09-11..09-19 that has few transactions and is reported
separately.
"""
import pandas as pd

from ..common.metrics import ranking_metrics


def score_baseline(frame, thresholds):
    """Apply the transparent rule-based AML risk score (the BQ1 baseline).

    Points are added for three independent signals:

    * +50 if the payment format is ACH (the heaviest single rule; on its own
      it is not enough to be flagged);
    * +20 if the prior-day USD amount (``amount_usd_asof``) is above the
      99th percentile for that payment format, as measured on the training
      period only (``thresholds``);
    * +30 for high velocity or fan-out: more than 10 payments in the previous
      hour, or more than 15 distinct counterparties in the previous 24 hours.

    The score is therefore one of 0, 20, 30, 50, 70, 80 or 100. A row is
    ``flagged`` when the score is at least 60, which in practice means
    "ACH plus at least one other signal".

    Args:
        frame: Curated transactions with behaviour features and
            ``amount_usd_asof``.
        thresholds: ``{payment_format: P99 amount}`` fitted on training data.
            Formats missing from the dict get an infinite threshold, i.e. can
            never trigger the amount rule.

    Returns:
        A copy of ``frame`` with ``risk_score``, ``risk_reasons`` (e.g.
        ``"ACH; high velocity or fan-out"``) and ``flagged``, sorted into
        review-queue order: highest score first, then oldest first, then file
        order - a fully deterministic ranking.
    """
    result = frame.copy()
    ach = result.payment_format.eq('ACH')
    # Look up each row's format-specific P99 threshold and compare.
    large = result.amount_usd_asof.gt(result.payment_format.map(thresholds).fillna(float('inf')))
    behaviour = result.txn_count_1h.gt(10) | result.distinct_counterparties_24h.gt(15)
    result['risk_score'] = ach.astype(int)*50 + large.astype(int)*20 + behaviour.astype(int)*30
    # Build an explanation string listing only the rules that fired.
    result['risk_reasons'] = [
        '; '.join(reason for flag, reason in zip(flags, ['ACH', 'above training P99', 'high velocity or fan-out']) if flag)
        for flags in zip(ach, large, behaviour)
    ]
    result['flagged'] = result.risk_score.ge(60)
    # kind='stable' guarantees identical ordering across runs and machines.
    return result.sort_values(['risk_score', 'timestamp', 'source_row_index'],
                              ascending=[False, True, True], kind='stable')


def evaluate_baseline(cohort):
    """Chronological bulk-data splits; report sparse late tail separately.

    Fits the amount thresholds on the training period only (before
    2022-09-07), scores the whole cohort, then reports ``ranking_metrics`` for
    each later period. Splitting by time (not randomly) mimics real use: the
    rules are set on the past and judged on the future.

    Args:
        cohort: Curated transactions with behaviour features,
            ``amount_usd_asof`` and labels.

    Returns:
        ``(ranked, thresholds, metrics)``: the fully scored and sorted
        cohort, the per-format P99 thresholds, and a dict of metrics keyed by
        ``validation``, ``test`` and ``sparse_tail``.

    Raises:
        ValueError: if there are no rows in the training period.
    """
    training = cohort.loc[cohort.timestamp < pd.Timestamp('2022-09-07')]
    if training.empty:
        raise ValueError('No training observations')
    # 99th percentile of prior-day USD amount for each payment format.
    thresholds = training.groupby('payment_format').amount_usd_asof.quantile(.99).to_dict()
    ranked = score_baseline(cohort, thresholds)
    metrics = {}
    # Each subset keeps the global queue order, so metrics are per-period queues.
    for split, start, end in [('validation', '2022-09-07', '2022-09-09'),
                              ('test', '2022-09-09', '2022-09-11'),
                              ('sparse_tail', '2022-09-11', '2022-09-19')]:
        subset = ranked.loc[ranked.timestamp.ge(start) & ranked.timestamp.lt(end)]
        metrics[split] = ranking_metrics(subset)
    return ranked, thresholds, metrics
