"""Score verified curated Parquet in batches and export full-population insights."""
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .analytics import concentration, corridor_totals, score_baseline
from .dashboard import ranked, records, treasury_view

FEATURES = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
            'distinct_counterparties_24h', 'distinct_counterparties_7d']
COLUMNS = ['source_row_index', 'transaction_id', 'timestamp', 'txn_date',
           'from_bank', 'from_account', 'to_bank', 'to_account', 'payment_format',
           'payment_currency', 'receiving_currency', 'amount_usd', 'amount_usd_asof',
           'is_laundering', *FEATURES]
QUEUE_COLUMNS = ['transaction_id', 'timestamp', 'from_bank', 'from_account',
                 'to_bank', 'to_account', 'payment_format', 'payment_currency',
                 'amount_usd_asof', 'risk_score', 'risk_reasons', 'txn_count_1h',
                 'distinct_counterparties_24h']
PERIODS = {'validation': ('2022-09-07', '2022-09-09'),
           'test': ('2022-09-09', '2022-09-11'), 'tail': ('2022-09-11', '2022-09-19')}


class AmlSummary:
    """Bounded top-K storage and exact metrics including tied-score AP."""
    def __init__(self):
        self.rows = self.positives = self.flagged = 0
        self.scores, self.hits = Counter(), Counter()
        self.daily_rows, self.daily_alerts = Counter(), Counter()
        self.queue = None

    def add(self, frame):
        if frame.empty:
            return
        self.rows += len(frame)
        self.positives += int(frame.is_laundering.sum())
        self.flagged += int(frame.flagged.sum())
        self.scores.update(frame.risk_score.value_counts().to_dict())
        self.hits.update(frame.groupby('risk_score').is_laundering.sum().to_dict())
        day = frame.timestamp.dt.strftime('%Y-%m-%d')
        self.daily_rows.update(day.value_counts().to_dict())
        self.daily_alerts.update(frame.assign(day=day).groupby('day').flagged.sum().to_dict())
        candidate = ranked(frame).head(100)
        self.queue = ranked(pd.concat([self.queue, candidate], ignore_index=True)).head(100) if self.queue is not None else candidate.copy()

    def view(self):
        k = min(100, self.rows)
        hits = int(self.queue.is_laundering.sum()) if self.queue is not None else 0
        precision = hits/k if k else None
        ap = None
        if self.positives:
            cumulative_rows = cumulative_hits = 0
            ap = 0.
            for score in sorted(self.scores, reverse=True):
                cumulative_rows += self.scores[score]
                cumulative_hits += self.hits[score]
                ap += self.hits[score]/self.positives * cumulative_hits/cumulative_rows
        metrics = dict(rows=self.rows, positives=self.positives, k=k, precision_at_k=precision,
                       recall_at_k=hits/self.positives if self.positives else None,
                       lift_at_k=precision/(self.positives/self.rows) if self.positives else None,
                       average_precision=ap)
        return dict(metrics=metrics, flagged=self.flagged,
                    queue=records(self.queue[QUEUE_COLUMNS]) if self.queue is not None else [],
                    daily=[dict(day=d, transactions=self.daily_rows[d], alerts=self.daily_alerts[d]) for d in sorted(self.daily_rows)],
                    histogram=[dict(risk_score=s, transactions=self.scores[s]) for s in sorted(self.scores)])


def fit_thresholds(dataset):
    """Exact pandas P99 convention, using training amounts only."""
    amounts = {}
    for batch in dataset.to_batches(columns=['timestamp', 'payment_format', 'amount_usd_asof'], batch_size=100_000):
        frame = batch.to_pandas()
        training = frame.loc[frame.timestamp.ge('2022-09-01') & frame.timestamp.lt('2022-09-07')]
        for name, group in training.groupby('payment_format'):
            amounts.setdefault(name, []).append(group.amount_usd_asof.to_numpy())
    if not amounts:
        raise ValueError('No training observations')
    return {name: float(np.quantile(np.concatenate(values), .99)) for name, values in amounts.items()}


def export_verified_batch(curated, verification, output, assets, *, serving_only=False):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory')
    proof = json.loads(Path(verification).read_text(encoding='utf-8'))
    if proof.get('status') != 'passed' or proof.get('rows', 0) <= 0:
        raise ValueError('Successful full-run verification is required')
    dataset = ds.dataset(curated, format='parquet', partitioning='hive')
    missing = set(COLUMNS) - set(dataset.schema.names)
    if missing:
        raise ValueError(f'Missing scoring columns: {sorted(missing)}')
    thresholds = fit_thresholds(dataset)
    summaries = {period: AmlSummary() for period in PERIODS}
    variants = {name: AmlSummary() for name in ['Current baseline', 'ACH only', 'ACH + amount', 'ACH + behaviour']}
    seen = np.zeros(proof['rows'], dtype=bool)
    aggregates, manifest = [], []
    rows = positives = 0
    output.mkdir(parents=True)
    writer = None
    try:
        for batch in dataset.to_batches(columns=COLUMNS, batch_size=100_000):
            frame = batch.to_pandas()
            indices = frame.source_row_index.to_numpy(dtype=np.int64)
            if ((indices < 0) | (indices >= len(seen))).any() or seen[indices].any() or len(np.unique(indices)) != len(indices):
                raise ValueError('Invalid or duplicate source IDs')
            if not frame.transaction_id.eq('HI-Small:' + frame.source_row_index.astype(str)).all():
                raise ValueError('Transaction ID mismatch')
            if not frame.is_laundering.isin([0, 1]).all() or frame[['amount_usd', 'amount_usd_asof', *FEATURES]].isna().any().any():
                raise ValueError('Invalid labels or missing scoring values')
            seen[indices] = True
            rows += len(frame)
            positives += int(frame.is_laundering.sum())
            aggregates.append(corridor_totals(frame))
            scored = score_baseline(frame, thresholds)
            table = pa.Table.from_pandas(scored, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(output / 'bq1_scored.parquet', table.schema, compression='snappy')
            writer.write_table(table)
            for period, (start, end) in PERIODS.items():
                summaries[period].add(scored.loc[scored.timestamp.ge(start) & scored.timestamp.lt(end)])
            validation = scored.loc[scored.timestamp.ge('2022-09-07') & scored.timestamp.lt('2022-09-09')]
            ach = validation.payment_format.eq('ACH').astype(int)*50
            amount = validation.amount_usd_asof.gt(validation.payment_format.map(thresholds).fillna(float('inf'))).astype(int)*20
            behaviour = (validation.txn_count_1h.gt(10) | validation.distinct_counterparties_24h.gt(15)).astype(int)*30
            for name, scores in [('Current baseline', ach+amount+behaviour), ('ACH only', ach),
                                 ('ACH + amount', ach+amount), ('ACH + behaviour', ach+behaviour)]:
                variants[name].add(validation.assign(risk_score=scores, flagged=scores.ge(60)))
            print(f'Scored {rows:,} verified transactions', flush=True)
    finally:
        if writer is not None:
            writer.close()
    if not seen.all() or rows != proof['rows'] or positives != proof['positives']:
        raise ValueError('Source counts differ from verification')
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    daily = pd.concat(aggregates, ignore_index=True).groupby(keys, observed=True)[['transaction_count', 'volume_usd']].sum().reset_index()
    if int(daily.transaction_count.sum()) != rows or not np.isclose(daily.volume_usd.sum(), proof['amount_usd'], rtol=1e-10, atol=.01):
        raise ValueError('Treasury totals do not reconcile')
    daily.to_parquet(output / 'bq2_daily_corridors.parquet', index=False)
    if not serving_only:
        concentration(daily).to_csv(output / 'concentration_daily.csv', index=False)
        daily.groupby(keys[1:])[['transaction_count', 'volume_usd']].sum().sort_values('volume_usd', ascending=False).to_csv(output / 'corridor_rankings.csv')
    aml = {name: summary.view() for name, summary in summaries.items()}
    if not serving_only:
        for name, view in aml.items():
            pd.DataFrame(view['queue'], columns=QUEUE_COLUMNS).to_csv(output / f'review_queue_{name}.csv', index=False)
    queue = summaries['validation'].queue
    breakdown = queue.groupby(['risk_score', 'risk_reasons']).agg(reviewed=('is_laundering', 'size'), positives=('is_laundering', 'sum')).reset_index() if queue is not None else pd.DataFrame(columns=['risk_score','risk_reasons','reviewed','positives'])
    breakdown['false_positives'] = breakdown.reviewed - breakdown.positives
    diagnostics = dict(period='2022-09-07 to 2022-09-08',
                       variants=[dict(variant=name, **summary.view()['metrics']) for name, summary in variants.items()],
                       top100_reasons=records(breakdown), decision='Exploratory validation comparison; no test-set retuning or automatic promotion.')
    daily['txn_date'] = pd.to_datetime(daily.txn_date).dt.strftime('%Y-%m-%d')
    treasury = {'all': treasury_view(daily)}
    trends = []
    for day, group in daily.groupby('txn_date'):
        treasury[day] = treasury_view(group)
        trends.append(dict(day=day, **{key: treasury[day][key] for key in ['transactions','volume_usd','hhi','top5_share']}))
    for name in ['bq1_scored.parquet', 'bq2_daily_corridors.parquet']:
        with (output / name).open('rb') as source:
            manifest.append(dict(file=name, sha256=hashlib.file_digest(source, 'sha256').hexdigest()))
    provenance = dict(curated=str(curated), verification_sha256=hashlib.sha256(Path(verification).read_bytes()).hexdigest(),
                      training_p99=thresholds, aml_scope='full', scoring_location='local export from verified AWS Glue output')
    snapshot = dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(), aml=aml,
                    treasury=treasury, treasury_daily=trends, diagnostics=diagnostics,
                    cohort_rows=rows, full_rows=rows, aml_scope='full', sources=manifest, provenance=provenance)
    dashboard = output / 'dashboard'
    dashboard.mkdir()
    (dashboard / 'dashboard.json').write_text(json.dumps(snapshot, allow_nan=False), encoding='utf-8')
    (dashboard / 'validation-diagnostics.json').write_text(json.dumps(diagnostics, indent=2, allow_nan=False), encoding='utf-8')
    if not serving_only:
        for name in ['index.html', 'dashboard.css', 'dashboard.js']:
            shutil.copyfile(Path(assets) / name, dashboard / name)
    report = dict(rows=rows, positives=positives, training_p99=thresholds,
                  metrics={name: view['metrics'] for name, view in aml.items()},
                  flagged={name: view['flagged'] for name, view in aml.items()},
                  treasury=treasury['all'], provenance=provenance)
    (output / 'run_report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    return report
