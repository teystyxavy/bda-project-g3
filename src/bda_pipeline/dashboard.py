"""Reproducible static dashboard snapshots and validation-only rule diagnostics."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .analytics import ranking_metrics


def records(frame):
    return json.loads(frame.to_json(orient='records', date_format='iso'))


def ranked(frame):
    return frame.sort_values(['risk_score', 'timestamp', 'source_row_index'],
                             ascending=[False, True, True], kind='stable')


def diagnose_rules(scored):
    training = scored.loc[scored.timestamp.lt('2022-09-07')]
    validation = scored.loc[scored.timestamp.ge('2022-09-07') & scored.timestamp.lt('2022-09-09')].copy()
    thresholds = training.groupby('payment_format').amount_usd_asof.quantile(.99)
    ach = validation.payment_format.eq('ACH').astype(int)*50
    amount = validation.amount_usd_asof.gt(validation.payment_format.map(thresholds).fillna(float('inf'))).astype(int)*20
    behaviour = (validation.txn_count_1h.gt(10) | validation.distinct_counterparties_24h.gt(15)).astype(int)*30
    variants = {'Current baseline': ach+amount+behaviour, 'ACH only': ach,
                'ACH + amount': ach+amount, 'ACH + behaviour': ach+behaviour}
    comparisons = []
    for name, scores in variants.items():
        metrics = ranking_metrics(ranked(validation.assign(risk_score=scores)))
        comparisons.append(dict(variant=name, **metrics))
    queue = ranked(validation).head(100)
    breakdown = queue.groupby(['risk_score', 'risk_reasons'], dropna=False).agg(
        reviewed=('is_laundering', 'size'), positives=('is_laundering', 'sum')).reset_index()
    breakdown['false_positives'] = breakdown.reviewed - breakdown.positives
    return dict(period='2022-09-07 to 2022-09-08', variants=comparisons,
                top100_reasons=records(breakdown),
                decision='Exploratory comparison only. Keep current baseline; no test-set retuning or automatic promotion.')


def aml_view(frame):
    ordered = ranked(frame)
    metrics = ranking_metrics(ordered)
    queue_columns = ['transaction_id', 'timestamp', 'from_bank', 'from_account', 'to_bank',
                     'to_account', 'payment_format', 'payment_currency', 'amount_usd_asof',
                     'risk_score', 'risk_reasons', 'txn_count_1h', 'distinct_counterparties_24h']
    daily = frame.assign(day=frame.timestamp.dt.strftime('%Y-%m-%d')).groupby('day').agg(
        transactions=('risk_score', 'size'), alerts=('flagged', 'sum')).reset_index()
    histogram = frame.groupby('risk_score').size().rename('transactions').reset_index()
    return dict(metrics=metrics, flagged=int(frame.flagged.sum()), queue=records(ordered.head(100)[queue_columns]),
                daily=records(daily), histogram=records(histogram))


def treasury_view(frame):
    banks = frame.groupby(['from_bank', 'to_bank'], observed=True).agg(
        volume_usd=('volume_usd', 'sum'), transactions=('transaction_count', 'sum')).reset_index()
    currencies = frame.groupby(['payment_currency', 'receiving_currency'], observed=True).agg(
        volume_usd=('volume_usd', 'sum'), transactions=('transaction_count', 'sum')).reset_index()
    total = float(banks.volume_usd.sum())
    shares = banks.volume_usd / total if total else banks.volume_usd * 0
    banks['share'] = shares
    currencies['share'] = currencies.volume_usd / total if total else 0
    return dict(transactions=int(frame.transaction_count.sum()), volume_usd=total,
                hhi=float((shares**2).sum()) if total else None,
                top5_share=float(shares.nlargest(5).sum()) if total else None,
                banks=records(banks.sort_values('volume_usd', ascending=False).head(15)),
                currencies=records(currencies.sort_values('volume_usd', ascending=False)),
                bank_pairs=len(banks))


def build_snapshot(scored, corridors):
    if scored.transaction_id.duplicated().any():
        raise ValueError('Duplicate transaction identities')
    if not scored.is_laundering.isin([0, 1]).all():
        raise ValueError('Labels must be binary')
    if corridors[['volume_usd', 'transaction_count']].isna().any().any():
        raise ValueError('Missing corridor measurements')
    aml = {}
    for name, start, end in [('validation','2022-09-07','2022-09-09'), ('test','2022-09-09','2022-09-11'),
                             ('tail','2022-09-11','2022-09-19')]:
        aml[name] = aml_view(scored.loc[scored.timestamp.ge(start) & scored.timestamp.lt(end)])
    corridors = corridors.copy()
    corridors['txn_date'] = pd.to_datetime(corridors.txn_date).dt.strftime('%Y-%m-%d')
    treasury = {'all': treasury_view(corridors)}
    daily = []
    for day, group in corridors.groupby('txn_date', observed=True):
        treasury[day] = treasury_view(group)
        daily.append(dict(day=day, **{key: treasury[day][key] for key in ['transactions','volume_usd','hhi','top5_share']}))
    diagnostics = diagnose_rules(scored)
    return dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
                aml=aml, treasury=treasury, treasury_daily=daily, diagnostics=diagnostics,
                cohort_rows=len(scored), full_rows=treasury['all']['transactions'])


def export_dashboard(scored_path, corridors_path, output):
    snapshot = build_snapshot(pd.read_parquet(scored_path), pd.read_parquet(corridors_path))
    snapshot['sources'] = [{'file': Path(path).name, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                           for path in [scored_path, corridors_path]]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / 'dashboard.json').write_text(json.dumps(snapshot, allow_nan=False), encoding='utf-8')
    (output / 'validation-diagnostics.json').write_text(json.dumps(snapshot['diagnostics'], indent=2, allow_nan=False), encoding='utf-8')
    return snapshot
