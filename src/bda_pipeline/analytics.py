"""Local batch analytics. Labels are used only by evaluation."""
from collections import Counter, deque

import numpy as np
import pandas as pd


def bank_key(values):
    return values.astype('string').str.lstrip('0').replace('', '0')


def add_behaviour(frame):
    """Exact outgoing windows [t-window,t); same-timestamp peers are excluded.

    Input must contain complete outgoing history for each included sender.
    History before the dataset starts is unavailable (cold start).
    """
    result = frame.copy().reset_index(drop=True)
    result['_bank'] = bank_key(result.from_bank)
    result['_to_bank'] = bank_key(result.to_bank)
    columns = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
               'distinct_counterparties_24h', 'distinct_counterparties_7d']
    features = np.zeros((len(result), 5), dtype=np.int64)
    for _, group in result.groupby(['_bank', 'from_account'], sort=False):
        group = group.sort_values(['timestamp', 'source_row_index'])
        queues = [deque(), deque(), deque()]
        counters = [Counter(), Counter(), Counter()]
        records = list(zip(group.index, group.timestamp, group._to_bank, group.to_account))
        pos = 0
        while pos < len(records):
            end = pos + 1
            now = records[pos][1]
            while end < len(records) and records[end][1] == now:
                end += 1
            for q, counts, hours in zip(queues, counters, [1, 24, 168]):
                cutoff = now - pd.Timedelta(hours=hours)
                while q and q[0][0] < cutoff:
                    _, party = q.popleft()
                    counts[party] -= 1
                    if counts[party] == 0:
                        del counts[party]
            for idx, _, _, _ in records[pos:end]:
                features[idx] = [len(q) for q in queues] + [len(counters[1]), len(counters[2])]
            for _, timestamp, bank, account in records[pos:end]:
                for q, counts in zip(queues, counters):
                    q.append((timestamp, (bank, account)))
                    counts[(bank, account)] += 1
            pos = end
    result[columns] = features
    return result.drop(columns=['_bank', '_to_bank'])


def score_baseline(frame, thresholds):
    result = frame.copy()
    ach = result.payment_format.eq('ACH')
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


def ranking_metrics(ranked, k=100):
    """Average precision (stepwise PR area), plus deterministic top-K metrics."""
    n = len(ranked)
    positives = int(ranked.is_laundering.sum())
    effective_k = min(k, n)
    hits = int(ranked.head(k).is_laundering.sum())
    precision = hits/effective_k if effective_k else None
    ap = None
    if positives:
        grouped = ranked.groupby('risk_score').is_laundering.agg(['sum', 'count']).sort_index(ascending=False)
        ap = float(((grouped['sum']/positives) * (grouped['sum'].cumsum()/grouped['count'].cumsum())).sum())
    return dict(rows=n, positives=positives, k=effective_k, precision_at_k=precision,
                recall_at_k=hits/positives if positives else None,
                lift_at_k=precision/(positives/n) if positives and precision is not None else None,
                average_precision=ap)


def evaluate_baseline(cohort):
    """Chronological bulk-data splits; report sparse late tail separately."""
    training = cohort.loc[cohort.timestamp < pd.Timestamp('2022-09-07')]
    if training.empty:
        raise ValueError('No training observations')
    thresholds = training.groupby('payment_format').amount_usd_asof.quantile(.99).to_dict()
    ranked = score_baseline(cohort, thresholds)
    metrics = {}
    for split, start, end in [('validation', '2022-09-07', '2022-09-09'),
                              ('test', '2022-09-09', '2022-09-11'),
                              ('sparse_tail', '2022-09-11', '2022-09-19')]:
        subset = ranked.loc[ranked.timestamp.ge(start) & ranked.timestamp.lt(end)]
        metrics[split] = ranking_metrics(subset)
    return ranked, thresholds, metrics


def corridor_totals(frame):
    frame = frame.assign(from_bank=bank_key(frame.from_bank), to_bank=bank_key(frame.to_bank))
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    return frame.groupby(keys, observed=True).agg(
        transaction_count=('amount_usd', 'size'), volume_usd=('amount_usd', 'sum')).reset_index()


def concentration(daily):
    """Daily directed bank-pair HHI (0..1), with currencies summed first."""
    bank = daily.groupby(['txn_date', 'from_bank', 'to_bank']).volume_usd.sum().reset_index()
    rows = []
    for day, group in bank.groupby('txn_date'):
        total = group.volume_usd.sum()
        shares = group.volume_usd / total if total > 0 else None
        rows.append(dict(txn_date=day, volume_usd=float(total),
                         hhi=float((shares**2).sum()) if shares is not None else None,
                         top5_share=float(shares.nlargest(5).sum()) if shares is not None else None))
    return pd.DataFrame(rows)
