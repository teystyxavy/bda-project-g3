"""Local batch analytics for BQ1 (AML risk review queue) and BQ2 (treasury corridor volume and concentration).
Labels are used only by evaluation; splits are chronological: train 09-01..09-07, validation 09-07..09-09, test 09-09..09-11, tail 09-11..09-19."""
from collections import Counter, deque

import numpy as np
import pandas as pd


def bank_key(values):
    return values.astype('string').str.lstrip('0').replace('', '0')


def _windowed_counts(result, self_bank, self_account, party_bank, party_account, amount_column):
    """Sliding-window counts, distinct counterparties and USD sums per entity for 1h/24h/7d, as an (n, 7) array:
    [count_1h, count_24h, count_7d, distinct_24h, distinct_7d, sum_24h, sum_7d] (sums skip the 1h window).
    Called keyed by sender (outgoing) and by receiver (incoming)."""
    features = np.zeros((len(result), 7), dtype=np.float64)
    for _, group in result.groupby([self_bank, self_account], sort=False):
        group = group.sort_values(['timestamp', 'source_row_index'])
        # Index 0/1/2 = the 1h / 24h / 7d windows respectively.
        queues = [deque(), deque(), deque()]
        counters = [Counter(), Counter(), Counter()]
        sums = [0.0, 0.0, 0.0]
        records = list(zip(group.index, group.timestamp, group[party_bank], group[party_account], group[amount_column]))
        pos = 0
        while pos < len(records):
            # Process rows sharing a timestamp together so none of them sees the others.
            end = pos + 1
            now = records[pos][1]
            while end < len(records) and records[end][1] == now:
                end += 1
            # Drop entries older than now - window; the cutoff itself is kept.
            for window in range(3):
                cutoff = now - pd.Timedelta(hours=[1, 24, 168][window])
                while queues[window] and queues[window][0][0] < cutoff:
                    _, party, amount = queues[window].popleft()
                    counters[window][party] -= 1
                    # Remove zero entries so len(counts) = distinct parties.
                    if counters[window][party] == 0:
                        del counters[window][party]
                    # Clamp at the eviction point so float drift never compounds across many cycles
                    # (a window that is actually empty must read back as exactly 0.0, not -1e-6).
                    sums[window] = max(0.0, sums[window] - amount)
            # Record features BEFORE adding the block (strict earlier-only); distinct = keys of the 24h/7d Counters.
            for idx, _, _, _, _ in records[pos:end]:
                features[idx] = ([len(q) for q in queues] + [len(counters[1]), len(counters[2])] +
                                 [sums[1], sums[2]])
            for _, timestamp, bank, account, amount in records[pos:end]:
                for window in range(3):
                    queues[window].append((timestamp, (bank, account), amount))
                    counters[window][(bank, account)] += 1
                    sums[window] += amount
            pos = end
    return features


def _prior_pair_counts(result, self_bank, self_account, party_bank, party_account):
    """All-time count of prior transactions between each self/party pair, scanned in chronological order per entity.
    0 = first time this directed pair has transacted (a novelty signal; no eviction, unlike the windowed counts)."""
    counts = np.zeros(len(result), dtype=np.int64)
    for _, group in result.groupby([self_bank, self_account], sort=False):
        group = group.sort_values(['timestamp', 'source_row_index'])
        seen = Counter()
        for idx, party_bank_value, party_account_value in zip(group.index, group[party_bank], group[party_account]):
            party = (party_bank_value, party_account_value)
            counts[idx] = seen[party]
            seen[party] += 1
    return counts


def add_behaviour(frame):
    """Adds 7 outgoing (sender) and 7 incoming (receiver) behaviour features over [t-window, t), plus one
    directed-pair novelty count. Same-timestamp peers are excluded; input needs complete history per account."""
    # reset_index so row labels are 0..n-1 and can be used to index `features`.
    result = frame.copy().reset_index(drop=True)
    # Zero-stripped bank IDs so "010" and "10" are grouped as the same bank.
    result['_from_bank'] = bank_key(result.from_bank)
    result['_to_bank'] = bank_key(result.to_bank)
    out_columns = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
                   'distinct_counterparties_24h', 'distinct_counterparties_7d',
                   'amount_sum_24h', 'amount_sum_7d']
    in_columns = ['in_txn_count_1h', 'in_txn_count_24h', 'in_txn_count_7d',
                  'distinct_senders_24h', 'distinct_senders_7d',
                  'in_amount_sum_24h', 'in_amount_sum_7d']
    result[out_columns] = _windowed_counts(result, '_from_bank', 'from_account', '_to_bank', 'to_account', 'amount_usd_asof')
    result[in_columns] = _windowed_counts(result, '_to_bank', 'to_account', '_from_bank', 'from_account', 'amount_usd_asof')
    result['corridor_prior_count'] = _prior_pair_counts(result, '_from_bank', 'from_account', '_to_bank', 'to_account')
    return result.drop(columns=['_from_bank', '_to_bank'])


def score_baseline(frame, thresholds):
    """Rule-based AML risk score: +50 ACH, +20 amount above the training P99, +30 high velocity or fan-out; flagged at >= 60.
    Returns a copy in deterministic review-queue order (score descending, oldest first, then file order)."""
    result = frame.copy()
    ach = result.payment_format.eq('ACH')
    # Per-format P99 threshold; formats without one can never trigger.
    large = result.amount_usd_asof.gt(result.payment_format.map(thresholds).fillna(float('inf')))
    behaviour = result.txn_count_1h.gt(10) | result.distinct_counterparties_24h.gt(15)
    result['risk_score'] = ach.astype(int)*50 + large.astype(int)*20 + behaviour.astype(int)*30
    result['risk_reasons'] = [
        '; '.join(reason for flag, reason in zip(flags, ['ACH', 'above training P99', 'high velocity or fan-out']) if flag)
        for flags in zip(ach, large, behaviour)
    ]
    result['flagged'] = result.risk_score.ge(60)
    return result.sort_values(['risk_score', 'timestamp', 'source_row_index'],
                              ascending=[False, True, True], kind='stable')


def ranking_metrics(ranked, k=2000):
    """Average precision (each risk_score level is one step, so ties do not matter) plus top-K precision, recall and lift.
    Metrics that would divide by zero are None."""
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
    """Fits per-format P99 amount thresholds before 2022-09-07, scores the cohort and reports metrics for each later period.
    Returns (ranked, thresholds, metrics); raises ValueError if the training period is empty."""
    training = cohort.loc[cohort.timestamp < pd.Timestamp('2022-09-07')]
    if training.empty:
        raise ValueError('No training observations')
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
    """Daily USD volume and transaction count per directed corridor (sending bank, receiving bank, currencies), with zero-stripped bank IDs.
    Can be called per chunk and summed later."""
    frame = frame.assign(from_bank=bank_key(frame.from_bank), to_bank=bank_key(frame.to_bank))
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    # observed=True: if any key is categorical, skip empty combinations.
    return frame.groupby(keys, observed=True).agg(
        transaction_count=('amount_usd', 'size'), volume_usd=('amount_usd', 'sum')).reset_index()


def concentration(daily):
    """Daily directed bank-pair HHI (sum of squared volume shares) and top-5 share; both are None when a day's volume is 0."""
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
