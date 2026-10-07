"""Local batch analytics. Labels are used only by evaluation.

This module holds the core analytical logic for both business questions, written
in plain pandas so it can be unit-tested and reused by several entry points:

BQ1 - AML risk review queue
    * ``add_behaviour`` computes per-sender velocity / fan-out features over
      rolling 1-hour, 24-hour and 7-day windows.
    * ``score_baseline`` applies the transparent, rule-based baseline score
      (ACH + large amount + behaviour) and attaches human-readable reasons.
    * ``ranking_metrics`` / ``evaluate_baseline`` measure how good a ranked
      queue is (precision@K, recall@K, lift, average precision) using the
      ``is_laundering`` label. The label is *never* used to compute a score;
      it is only read here, for evaluation.

BQ2 - Treasury corridor volume and concentration
    * ``corridor_totals`` sums USD volume per day and directed bank pair /
      currency pair ("corridor").
    * ``concentration`` turns daily corridor volumes into an HHI and top-5 share.

Who calls it:
    * ``batch.py`` (``export_verified_batch``) scores the full curated dataset
      in chunks with ``score_baseline``, ``corridor_totals`` and
      ``concentration`` to build the dashboard export and ``bq1_scored.parquet``.
    * ``model.py`` and the dashboard builders use ``ranking_metrics`` so the
      XGBoost model and the rule baseline are compared with the same metric.
    * ``ingest_compute.py`` and the validation scripts call ``add_behaviour``
      to recompute the Glue-produced behaviour features as a cross-check.
    * ``scripts/local/run-local-analysis.py`` / ``evaluate-local-risk.py`` run
      everything on a laptop.

Chronological splits used throughout the project (timestamps, end exclusive):
train 2022-09-01..09-07, validation 09-07..09-09, test 09-09..09-11, and a
sparse late "tail" 09-11..09-19 that has few transactions and is reported
separately.
"""
from collections import Counter, deque

import numpy as np
import pandas as pd


def bank_key(values):
    """Normalise bank IDs for comparison by stripping leading zeroes.

    The raw transaction file writes some bank IDs zero-padded (``"010"``)
    while other sources write ``"10"``; both must be treated as the same bank.
    An all-zero ID would become an empty string, so it is mapped back to
    ``"0"``.

    Args:
        values: A pandas Series of bank identifiers (any dtype).

    Returns:
        A pandas ``string`` Series of normalised bank IDs.
    """
    return values.astype('string').str.lstrip('0').replace('', '0')


def add_behaviour(frame):
    """Exact outgoing windows [t-window,t); same-timestamp peers are excluded.

    Input must contain complete outgoing history for each included sender.
    History before the dataset starts is unavailable (cold start).

    For every transaction, looks back over the *same sender's* earlier
    outgoing transactions and counts:

    * ``txn_count_1h`` / ``txn_count_24h`` / ``txn_count_7d`` - how many
      payments the sender made in the previous 1 hour, 24 hours and 7 days
      (velocity: bursts of activity are a classic laundering signal);
    * ``distinct_counterparties_24h`` / ``distinct_counterparties_7d`` - how
      many different receiving (bank, account) pairs the sender paid in the
      previous 24 hours and 7 days (fan-out: spreading money across many
      accounts).

    "Previous" is strict: the window is [t - window, t), so the current
    transaction and any other transactions with the *identical* timestamp are
    not counted. This avoids leaking information from simultaneous rows whose
    order in the file is arbitrary.

    Algorithm: a sliding window per sender. Rows are visited in time order;
    each window keeps a deque of (timestamp, counterparty) entries plus a
    Counter of counterparties. Old entries are popped from the left as the
    window moves, so each row is pushed and popped at most once per window
    (roughly linear time per sender instead of quadratic).

    Args:
        frame: Transactions with ``from_bank, from_account, to_bank,
            to_account, timestamp, source_row_index``.

    Returns:
        A copy of ``frame`` (index reset to 0..n-1) with the five int64
        feature columns added.
    """
    # reset_index so row labels are 0..n-1 and can be used to index `features`.
    result = frame.copy().reset_index(drop=True)
    # Zero-stripped bank IDs so "010" and "10" are grouped as the same bank.
    result['_bank'] = bank_key(result.from_bank)
    result['_to_bank'] = bank_key(result.to_bank)
    columns = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
               'distinct_counterparties_24h', 'distinct_counterparties_7d']
    # One row of 5 feature values per transaction, filled in below.
    features = np.zeros((len(result), 5), dtype=np.int64)
    # A "sender" is a (bank, account) pair; windows are computed independently.
    for _, group in result.groupby(['_bank', 'from_account'], sort=False):
        # source_row_index breaks timestamp ties deterministically.
        group = group.sort_values(['timestamp', 'source_row_index'])
        # Index 0/1/2 = the 1h / 24h / 7d windows respectively.
        queues = [deque(), deque(), deque()]
        counters = [Counter(), Counter(), Counter()]
        records = list(zip(group.index, group.timestamp, group._to_bank, group.to_account))
        pos = 0
        while pos < len(records):
            # Find the block [pos, end) of rows that share the same timestamp;
            # they are processed together so none of them sees the others.
            end = pos + 1
            now = records[pos][1]
            while end < len(records) and records[end][1] == now:
                end += 1
            # Slide each window: drop entries older than now - window
            # (168 hours = 7 days). Entries exactly at the cutoff are kept,
            # which makes the lower bound inclusive.
            for q, counts, hours in zip(queues, counters, [1, 24, 168]):
                cutoff = now - pd.Timedelta(hours=hours)
                while q and q[0][0] < cutoff:
                    _, party = q.popleft()
                    counts[party] -= 1
                    # Remove zero entries so len(counts) = distinct parties.
                    if counts[party] == 0:
                        del counts[party]
            # Record features for every row in the same-timestamp block BEFORE
            # adding the block to the windows (strict "earlier only" rule).
            # Counts = deque lengths; distinct = keys in the 24h and 7d Counters.
            for idx, _, _, _ in records[pos:end]:
                features[idx] = [len(q) for q in queues] + [len(counters[1]), len(counters[2])]
            # Now add the block so later rows can see it.
            for _, timestamp, bank, account in records[pos:end]:
                for q, counts in zip(queues, counters):
                    q.append((timestamp, (bank, account)))
                    counts[(bank, account)] += 1
            pos = end
    result[columns] = features
    return result.drop(columns=['_bank', '_to_bank'])


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


def ranking_metrics(ranked, k=100):
    """Average precision (stepwise PR area), plus deterministic top-K metrics.

    "Top-K" mirrors the business setting: a compliance team can only review
    about K=100 alerts, so what matters is how many true laundering cases land
    at the top of the queue.

    Args:
        ranked: Rows already sorted into queue order, with ``risk_score`` and
            the 0/1 ``is_laundering`` label.
        k: Queue size to evaluate (default 100).

    Returns:
        dict with:
        ``rows`` and ``positives`` (laundering count);
        ``k`` (K, capped at the number of rows);
        ``precision_at_k`` = hits in top K / K;
        ``recall_at_k`` = hits in top K / all positives;
        ``lift_at_k`` = precision@K / overall positive rate (how many times
        better than picking K rows at random);
        ``average_precision`` - area under the precision-recall curve.
        Metrics that would divide by zero are ``None``.

    AP and ties: rows with equal ``risk_score`` cannot be meaningfully ordered
    (the rule score has only a few distinct values), so AP treats each score
    level as one step. For each level, from highest score down:
    ``(positives at this level / total positives) * (cumulative precision
    after including the whole level)``. This makes AP independent of the
    arbitrary order inside a tie.
    """
    n = len(ranked)
    positives = int(ranked.is_laundering.sum())
    effective_k = min(k, n)
    hits = int(ranked.head(k).is_laundering.sum())
    precision = hits/effective_k if effective_k else None
    ap = None
    if positives:
        # One row per distinct score, highest first: sum = positives, count = rows.
        grouped = ranked.groupby('risk_score').is_laundering.agg(['sum', 'count']).sort_index(ascending=False)
        # recall gained at this level * precision at the end of this level.
        ap = float(((grouped['sum']/positives) * (grouped['sum'].cumsum()/grouped['count'].cumsum())).sum())
    return dict(rows=n, positives=positives, k=effective_k, precision_at_k=precision,
                recall_at_k=hits/positives if positives else None,
                lift_at_k=precision/(positives/n) if positives and precision is not None else None,
                average_precision=ap)


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


def corridor_totals(frame):
    """Aggregate USD volume per daily corridor (BQ2).

    A corridor is a directed route: sending bank -> receiving bank, together
    with the payment and receiving currencies. Bank IDs are zero-stripped first
    so ``"010"`` and ``"10"`` fall into the same corridor. This is the pandas
    equivalent of the Athena corridor SQL.

    Args:
        frame: Curated transactions with ``txn_date`` and ``amount_usd``.

    Returns:
        One row per (txn_date, from_bank, to_bank, payment_currency,
        receiving_currency) with ``transaction_count`` and ``volume_usd``.
        Because batches can be aggregated separately and summed later,
        ``batch.py`` calls this chunk by chunk.
    """
    frame = frame.assign(from_bank=bank_key(frame.from_bank), to_bank=bank_key(frame.to_bank))
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    # observed=True: if any key is categorical, skip empty combinations.
    return frame.groupby(keys, observed=True).agg(
        transaction_count=('amount_usd', 'size'), volume_usd=('amount_usd', 'sum')).reset_index()


def concentration(daily):
    """Daily directed bank-pair HHI (0..1), with currencies summed first.

    The Herfindahl-Hirschman Index measures how concentrated a market is:
    ``HHI = sum(share_i ** 2)`` where ``share_i`` is bank pair i's fraction of
    that day's total USD volume. HHI near 0 means volume is spread over many
    bank pairs; HHI = 1 means one bank pair carries everything. ``top5_share``
    is the combined share of the five largest bank pairs - an easier number to
    explain to treasury.

    Args:
        daily: Output of ``corridor_totals`` (one row per corridor and day).

    Returns:
        DataFrame with ``txn_date, volume_usd, hhi, top5_share`` per day.
        If a day's total volume is 0, ``hhi`` and ``top5_share`` are ``None``
        (shares would be undefined).
    """
    # Collapse currency pairs so each directed bank pair is one "player".
    bank = daily.groupby(['txn_date', 'from_bank', 'to_bank']).volume_usd.sum().reset_index()
    rows = []
    for day, group in bank.groupby('txn_date'):
        total = group.volume_usd.sum()
        shares = group.volume_usd / total if total > 0 else None
        rows.append(dict(txn_date=day, volume_usd=float(total),
                         hhi=float((shares**2).sum()) if shares is not None else None,
                         top5_share=float(shares.nlargest(5).sum()) if shares is not None else None))
    return pd.DataFrame(rows)
