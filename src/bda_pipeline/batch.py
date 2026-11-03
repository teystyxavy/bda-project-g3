"""Scores the verified curated Parquet in 100k-row batches (BQ1 rule baseline, BQ2 corridor totals) and exports the dashboard files.
Called by ``ingest_compute.baseline_stage`` (``serving_only=True`` on AWS); needs a passed ``proof.json`` and a fresh output directory."""
import hashlib
import json
import shutil
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .analytics import concentration, corridor_totals, score_baseline
from .dashboard import ranked, records, treasury_view
from .splits import eval_names, row_share_splits, window_label

# Causal behaviour features from the curate job: 7 for the sender's outgoing activity (counts, distinct
# counterparties, rolling USD sums), then 7 for the receiver's incoming activity, then one directed-pair
# novelty count. They only look backwards in time, so they never leak the future.
FEATURES = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
            'distinct_counterparties_24h', 'distinct_counterparties_7d',
            'amount_sum_24h', 'amount_sum_7d',
            'in_txn_count_1h', 'in_txn_count_24h', 'in_txn_count_7d',
            'distinct_senders_24h', 'distinct_senders_7d',
            'in_amount_sum_24h', 'in_amount_sum_7d',
            'corridor_prior_count']
# Which cohort can prove each feature: a sender's complete history recomputes its outgoing and directed-pair features,
# a receiver's complete history its incoming ones. Derived by name so the split cannot drift when FEATURES changes.
RECEIVER_FEATURES = [name for name in FEATURES if name.startswith(('in_', 'distinct_senders'))]
SENDER_FEATURES = [name for name in FEATURES if name not in RECEIVER_FEATURES]
# Columns read from the curated Parquet; a missing one fails fast.
COLUMNS = ['source_row_index', 'transaction_id', 'timestamp', 'txn_date',
           'from_bank', 'from_account', 'to_bank', 'to_account', 'payment_format',
           'payment_currency', 'receiving_currency', 'amount_usd', 'amount_usd_asof',
           'is_laundering', *FEATURES]
# Review-queue columns; the label is deliberately excluded because production has none.
QUEUE_COLUMNS = ['transaction_id', 'timestamp', 'from_bank', 'from_account',
                 'to_bank', 'to_account', 'payment_format', 'payment_currency',
                 'amount_usd_asof', 'risk_score', 'risk_reasons', 'txn_count_1h',
                 'distinct_counterparties_24h', 'amount_sum_7d', 'corridor_prior_count']
def evaluation_periods(splits, end_date):
    """Every non-train window of ``splits`` (half-open), with the last one stretched to include the run's last day."""
    end = (datetime.fromisoformat(end_date) + timedelta(days=1)).date().isoformat()
    periods = {name: tuple(splits[name]) for name in eval_names(splits)}
    last = list(periods)[-1]
    periods[last] = (periods[last][0], max(periods[last][1], end))
    return periods


def daily_row_counts(dataset):
    """Rows per ``txn_date``: from Parquet metadata for hive partitions, otherwise by counting the column."""
    counts = Counter()
    for fragment in dataset.get_fragments():
        keys = ds.get_partition_keys(fragment.partition_expression)
        if 'txn_date' in keys:
            counts[str(keys['txn_date'])] += fragment.count_rows()
        else:
            counts.update(fragment.to_table(columns=['txn_date']).to_pandas().txn_date.astype(str).value_counts().to_dict())
    return dict(counts)


class AmlSummary:
    """Bounded top-2000 queue plus running counters for one period, giving exact metrics including tied-score AP.
    Never stores the full scored data."""
    def __init__(self):
        """Starts empty (``queue`` is None until a batch is added)."""
        self.rows = self.positives = self.flagged = 0
        self.scores, self.hits = Counter(), Counter()
        self.daily_rows, self.daily_alerts = Counter(), Counter()
        self.queue = None

    def add(self, frame):
        """Folds one scored batch (already filtered to this period) into the counters and refreshes the top-2000 queue."""
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
        # The global top 2000 lies within (old top 2000 + this batch's top 2000), so merging and re-ranking is exact.
        candidate = ranked(frame).head(2000)
        self.queue = ranked(pd.concat([self.queue, candidate], ignore_index=True)).head(2000) if self.queue is not None else candidate.copy()

    def view(self):
        """Dashboard-ready summary: metrics (precision, recall and lift at k=2000 or fewer, average precision), flagged count,
        top-2000 queue, daily counts and score histogram."""
        k = min(2000, self.rows)
        hits = int(self.queue.is_laundering.sum()) if self.queue is not None else 0
        precision = hits/k if k else None
        ap = None
        if self.positives:
            # Tied-score AP: each score block adds its share of positives x cumulative precision (matches analytics.ranking_metrics).
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


def fit_thresholds(dataset, train_window):
    """Per-payment-format P99 of ``amount_usd_asof`` over the half-open ``train_window``, so later periods never leak in.
    Uses one exact ``np.quantile`` per format; raises ValueError if there are no training rows."""
    amounts = {}
    for batch in dataset.to_batches(columns=['timestamp', 'payment_format', 'amount_usd_asof'], batch_size=100_000):
        frame = batch.to_pandas()
        training = frame.loc[frame.timestamp.ge(train_window[0]) & frame.timestamp.lt(train_window[1])]
        for name, group in training.groupby('payment_format'):
            amounts.setdefault(name, []).append(group.amount_usd_asof.to_numpy())
    if not amounts:
        raise ValueError('No training observations')
    return {name: float(np.quantile(np.concatenate(values), .99)) for name, values in amounts.items()}


def export_verified_batch(curated, verification, output, assets, *, serving_only=False, subset='HI-Small', end_date='2022-09-18', splits=None):
    """Score the full verified curated population and write BQ1/BQ2 outputs.

    Args:
        curated: path to the hive-partitioned curated Parquet directory.
        verification: path to ``proof.json`` from ``verify_curated``. Scoring
            refuses to run unless this proof says the curated data passed.
        output: directory to create. It must not exist yet, so an old run's
            files can never be mixed with a new run's files.
        assets: directory holding the static dashboard HTML/CSS/JS.
        serving_only: when True (the AWS path), write only what the dashboard
            and later verification need; skip CSV reports and asset copies.
        splits: chronological windows (see ``splits.py``); defaults to
            ``row_share_splits`` over the daily row counts, and is recorded in the snapshot.

    Returns:
        The ``run_report.json`` content as a dict (row counts, P99
        thresholds, per-period metrics, flagged counts, treasury totals and
        provenance).

    Raises:
        ValueError: on any integrity failure (missing proof, missing columns,
        duplicate/invalid row IDs, bad labels, missing values, row or USD
        totals that do not reconcile with the proof). The job fails loudly
        rather than publishing a partial or inconsistent dashboard.
    """
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh output directory')
    # Only score data that passed verification.
    proof = json.loads(Path(verification).read_text(encoding='utf-8'))
    if proof.get('status') != 'passed' or proof.get('rows', 0) <= 0:
        raise ValueError('Successful full-run verification is required')
    # hive partitioning restores the txn_date column.
    dataset = ds.dataset(curated, format='parquet', partitioning='hive')
    missing = set(COLUMNS) - set(dataset.schema.names)
    if missing:
        raise ValueError(f'Missing scoring columns: {sorted(missing)}')
    splits = splits or row_share_splits(daily_row_counts(dataset))
    thresholds = fit_thresholds(dataset, splits['train'])
    periods = evaluation_periods(splits, end_date)
    validation_window = periods['validation']
    # One summary per evaluation period, plus one per rule variant for the
    # validation-only ablation ("which rule components actually help?").
    summaries = {period: AmlSummary() for period in periods}
    variants = {name: AmlSummary() for name in ['Current baseline', 'ACH only', 'ACH + amount', 'ACH + behaviour']}
    # One boolean per source CSV row: proves every row is scored exactly once.
    seen = np.zeros(proof['rows'], dtype=bool)
    aggregates, manifest = [], []
    rows = positives = 0
    output.mkdir(parents=True)
    writer = None
    try:
        for batch in dataset.to_batches(columns=COLUMNS, batch_size=100_000):
            frame = batch.to_pandas()
            # source_row_index must be in range, unseen and unique.
            indices = frame.source_row_index.to_numpy(dtype=np.int64)
            if ((indices < 0) | (indices >= len(seen))).any() or seen[indices].any() or len(np.unique(indices)) != len(indices):
                raise ValueError('Invalid or duplicate source IDs')
            # transaction_id is derived as "HI-Small:<row index>"; a mismatch
            # would mean rows were shuffled or IDs corrupted in curation.
            if not frame.transaction_id.eq(subset + ':' + frame.source_row_index.astype(str)).all():
                raise ValueError('Transaction ID mismatch')
            if not frame.is_laundering.isin([0, 1]).all() or frame[['amount_usd', 'amount_usd_asof', *FEATURES]].isna().any().any():
                raise ValueError('Invalid labels or missing scoring values')
            seen[indices] = True
            rows += len(frame)
            positives += int(frame.is_laundering.sum())
            # BQ2: partial corridor totals for this batch; summed at the end.
            aggregates.append(corridor_totals(frame))
            if len(aggregates) >= 8:
                keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
                aggregates = [pd.concat(aggregates, ignore_index=True).groupby(keys, observed=True)[['transaction_count', 'volume_usd']].sum().reset_index()]
            # BQ1: score = 50 (ACH) + 20 (above training P99) + 30 (velocity
            # or fan-out); flagged when score >= 60. See analytics.score_baseline.
            scored = score_baseline(frame, thresholds)
            # Append to one Parquet file; the schema comes from the first batch.
            table = pa.Table.from_pandas(scored, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(output / 'bq1_scored.parquet', table.schema, compression='snappy')
            writer.write_table(table)
            for period, (start, end) in periods.items():
                summaries[period].add(scored.loc[scored.timestamp.ge(start) & scored.timestamp.lt(end)])
            # Validation-only ablation (same weights as score_baseline); test data never chooses rules.
            validation = scored.loc[scored.timestamp.ge(validation_window[0]) & scored.timestamp.lt(validation_window[1])]
            ach = validation.payment_format.eq('ACH').astype(int)*50
            # Formats unseen in training can never trigger the amount rule.
            amount = validation.amount_usd_asof.gt(validation.payment_format.map(thresholds).fillna(float('inf'))).astype(int)*20
            # Behaviour rule: >10 sends in 1h or >15 distinct receivers in 24h.
            behaviour = (validation.txn_count_1h.gt(10) | validation.distinct_counterparties_24h.gt(15)).astype(int)*30
            for name, scores in [('Current baseline', ach+amount+behaviour), ('ACH only', ach),
                                 ('ACH + amount', ach+amount), ('ACH + behaviour', ach+behaviour)]:
                variants[name].add(validation.assign(risk_score=scores, flagged=scores.ge(60)))
            print(f'Scored {rows:,} verified transactions', flush=True)
    finally:
        # Close the writer so the Parquet footer is written even if a check failed.
        if writer is not None:
            writer.close()
    # Every source row scored once, with row and positive totals matching the proof.
    if not seen.all() or rows != proof['rows'] or positives != proof['positives']:
        raise ValueError('Source counts differ from verification')
    # A corridor can span batches, so regroup and sum.
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    daily = pd.concat(aggregates, ignore_index=True).groupby(keys, observed=True)[['transaction_count', 'volume_usd']].sum().reset_index()
    # Corridor totals must reconcile with the verified rows and USD total (tolerance for float order).
    if int(daily.transaction_count.sum()) != rows or not np.isclose(daily.volume_usd.sum(), proof['amount_usd'], rtol=1e-10, atol=.01):
        raise ValueError('Treasury totals do not reconcile')
    daily.to_parquet(output / 'bq2_daily_corridors.parquet', index=False)
    if not serving_only:
        # Extra analyst reports: daily HHI / top-5 share and all-time corridor ranking.
        concentration(daily).to_csv(output / 'concentration_daily.csv', index=False)
        daily.groupby(keys[1:])[['transaction_count', 'volume_usd']].sum().sort_values('volume_usd', ascending=False).to_csv(output / 'corridor_rankings.csv')
    aml = {name: summary.view() for name, summary in summaries.items()}
    if not serving_only:
        for name, view in aml.items():
            pd.DataFrame(view['queue'], columns=QUEUE_COLUMNS).to_csv(output / f'review_queue_{name}.csv', index=False)
    # Validation top-2000 breakdown by (score, reasons): reviewed, true and false positives.
    queue = summaries['validation'].queue
    breakdown = queue.groupby(['risk_score', 'risk_reasons']).agg(reviewed=('is_laundering', 'size'), positives=('is_laundering', 'sum')).reset_index() if queue is not None else pd.DataFrame(columns=['risk_score','risk_reasons','reviewed','positives'])
    breakdown['false_positives'] = breakdown.reviewed - breakdown.positives
    diagnostics = dict(period=window_label(validation_window),
                       variants=[dict(variant=name, **summary.view()['metrics']) for name, summary in variants.items()],
                       top100_reasons=records(breakdown), decision='Exploratory validation comparison; no test-set retuning or automatic promotion.')
    # BQ2 views: one for the whole period plus one per day, and a daily trend list.
    daily['txn_date'] = pd.to_datetime(daily.txn_date).dt.strftime('%Y-%m-%d')
    treasury = {'all': treasury_view(daily)}
    trends = []
    for day, group in daily.groupby('txn_date'):
        treasury[day] = treasury_view(group)
        trends.append(dict(day=day, **{key: treasury[day][key] for key in ['transactions','volume_usd','hhi','top5_share']}))
    # SHA-256 of each data file and the proof, so the dashboard traces to exactly these outputs.
    for name in ['bq1_scored.parquet', 'bq2_daily_corridors.parquet']:
        with (output / name).open('rb') as source:
            manifest.append(dict(file=name, sha256=hashlib.file_digest(source, 'sha256').hexdigest()))
    provenance = dict(curated=str(curated), verification_sha256=hashlib.sha256(Path(verification).read_bytes()).hexdigest(),
                      training_p99=thresholds, aml_scope='full', scoring_location='local export from verified AWS Glue output')
    # schema_version=1 is the snapshot format the dashboard.js front end reads.
    snapshot = dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(), aml=aml,
                    treasury=treasury, treasury_daily=trends, diagnostics=diagnostics,
                    cohort_rows=rows, full_rows=rows, aml_scope='full', sources=manifest, provenance=provenance,
                    splits=splits)
    dashboard = output / 'dashboard'
    dashboard.mkdir()
    # allow_nan=False: NaN is invalid JSON and would break the browser parser.
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
