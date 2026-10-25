"""Score verified curated Parquet in batches and export full-population insights.

Where this sits in the pipeline
-------------------------------
The Glue curate job (``jobs/curate_transactions.py``) writes the curated,
date-partitioned Parquet dataset (one row per IBM AML transaction, already
converted to USD and enriched with causal behaviour features). Before anything
is published, ``ingest_compute.verify_curated`` re-checks that dataset against
the raw Kaggle CSV and FX rates and writes a ``proof.json``. This module is the
next step: it streams the *whole* curated population through the transparent
rule baseline (BQ1) and the corridor aggregation (BQ2), and exports the files
the dashboard and later verification steps need.

Called by ``ingest_compute.baseline_stage`` inside the network-isolated
SageMaker Processing container (``sagemaker/pipeline_worker.py``) with
``serving_only=True``. It can also be called locally on a downloaded copy of
the curated output to produce the extra CSV reports.

Reads
    * ``curated``: hive-partitioned Parquet directory (``txn_date=YYYY-MM-DD/``).
    * ``verification``: the ``proof.json`` written by ``verify_curated``; must
      say ``status == 'passed'`` and carry the row/positive/USD totals.
    * ``assets``: folder containing the static dashboard shell
      (``index.html``, ``dashboard.css``, ``dashboard.js``).

Writes (all inside a *fresh* ``output`` directory)
    * ``bq1_scored.parquet``          every transaction with risk_score/reasons/flagged.
    * ``bq2_daily_corridors.parquet`` daily bank-pair x currency-pair USD totals.
    * ``dashboard/dashboard.json`` and ``dashboard/validation-diagnostics.json``.
    * ``run_report.json``             compact summary of metrics and provenance.
    * Only when ``serving_only`` is False: ``concentration_daily.csv``,
      ``corridor_rankings.csv``, ``review_queue_<period>.csv`` and a copy of
      the dashboard HTML/CSS/JS.

Memory design: the data (~5M rows for HI-Small) is processed 100,000 rows at a
time. Only small running counters and a bounded top-100 queue are kept per
period, so the job fits in a modest container instead of loading everything
into one pandas DataFrame.
"""
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

from ..baseline.batch import COLUMNS, PERIODS, QUEUE_COLUMNS, fit_thresholds
from ..baseline.rules import score_baseline
from ..dashboards.dashboard import ranked, records, treasury_view
from ..features.behaviour import FEATURES
from ..treasury.corridors import concentration, corridor_totals


class AmlSummary:
    """Bounded top-K storage and exact metrics including tied-score AP.

    One instance accumulates results for one evaluation period (or one rule
    variant) across many 100k-row batches. It never stores the full scored
    data. Instead it keeps:

    * ``rows`` / ``positives`` / ``flagged``: running totals.
    * ``scores``: Counter of risk_score -> number of transactions with it.
    * ``hits``: Counter of risk_score -> number of true laundering rows with it.
    * ``daily_rows`` / ``daily_alerts``: per-day transaction and alert counts
      for the dashboard's daily chart.
    * ``queue``: the current best 100 rows (the analyst review queue).

    Because the rule baseline only produces a handful of distinct scores
    (sums of 0/20/30/50), the score/hit Counters are enough to compute average
    precision exactly, treating all rows with an equal score as one tied block.
    """
    def __init__(self):
        """Start with empty counters and no queue (``None`` = nothing seen yet)."""
        self.rows = self.positives = self.flagged = 0
        self.scores, self.hits = Counter(), Counter()
        self.daily_rows, self.daily_alerts = Counter(), Counter()
        self.queue = None

    def add(self, frame):
        """Fold one scored batch (already filtered to this period) into the summary.

        Args:
            frame: pandas DataFrame with at least ``is_laundering``, ``flagged``,
                ``risk_score``, ``timestamp`` and the ranking/queue columns.

        Side effects: updates the counters in place and refreshes ``self.queue``
        so it always holds the top 100 rows seen so far.
        """
        if frame.empty:
            return
        self.rows += len(frame)
        self.positives += int(frame.is_laundering.sum())
        self.flagged += int(frame.flagged.sum())
        # Histogram of scores, and how many true positives fall on each score.
        self.scores.update(frame.risk_score.value_counts().to_dict())
        self.hits.update(frame.groupby('risk_score').is_laundering.sum().to_dict())
        # Per-day volume and alert counts (alert = flagged, i.e. score >= 60).
        day = frame.timestamp.dt.strftime('%Y-%m-%d')
        self.daily_rows.update(day.value_counts().to_dict())
        self.daily_alerts.update(frame.assign(day=day).groupby('day').flagged.sum().to_dict())
        # Streaming top-K: the global top 100 must be inside (old top 100 +
        # this batch's top 100), so merging the two and re-ranking is exact.
        # ranked() sorts by score desc, then timestamp and source row asc, so
        # ties are broken deterministically (oldest transaction first).
        candidate = ranked(frame).head(100)
        self.queue = ranked(pd.concat([self.queue, candidate], ignore_index=True)).head(100) if self.queue is not None else candidate.copy()

    def view(self):
        """Return the dashboard-ready summary for this period.

        Returns:
            dict with ``metrics`` (rows, positives, k, precision/recall/lift at
            k and average precision), ``flagged`` count, the top-100 ``queue``
            as JSON records, a ``daily`` list and a score ``histogram``.

        Metric definitions (k = 100 or fewer if the period is smaller):
            precision@k = true positives in queue / k
            recall@k    = true positives in queue / all positives in period
            lift@k      = precision@k / base rate (positives / rows), i.e. how
                          many times better than reviewing random transactions.
        """
        k = min(100, self.rows)
        hits = int(self.queue.is_laundering.sum()) if self.queue is not None else 0
        precision = hits/k if k else None
        ap = None
        if self.positives:
            # Tied-score average precision: walk score blocks from highest to
            # lowest. Each block contributes (its share of all positives) x
            # (cumulative precision after including the whole block). This
            # matches common.metrics.ranking_metrics and does not depend on how
            # ties happen to be ordered.
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


def export_verified_batch(curated, verification, output, assets, *, serving_only=False):
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
    # Gate: only score data that the independent verification step accepted.
    proof = json.loads(Path(verification).read_text(encoding='utf-8'))
    if proof.get('status') != 'passed' or proof.get('rows', 0) <= 0:
        raise ValueError('Successful full-run verification is required')
    # partitioning='hive' turns the txn_date=YYYY-MM-DD folder names back
    # into a txn_date column.
    dataset = ds.dataset(curated, format='parquet', partitioning='hive')
    missing = set(COLUMNS) - set(dataset.schema.names)
    if missing:
        raise ValueError(f'Missing scoring columns: {sorted(missing)}')
    thresholds = fit_thresholds(dataset)
    # One summary per evaluation period, plus one per rule variant for the
    # validation-only ablation ("which rule components actually help?").
    summaries = {period: AmlSummary() for period in PERIODS}
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
            # --- Integrity checks on every batch ---
            # source_row_index is the 0-based row number in the raw CSV. It
            # must be in range, not seen in an earlier batch, and unique
            # within this batch.
            indices = frame.source_row_index.to_numpy(dtype=np.int64)
            if ((indices < 0) | (indices >= len(seen))).any() or seen[indices].any() or len(np.unique(indices)) != len(indices):
                raise ValueError('Invalid or duplicate source IDs')
            # transaction_id is derived as "HI-Small:<row index>"; a mismatch
            # would mean rows were shuffled or IDs corrupted in curation.
            if not frame.transaction_id.eq('HI-Small:' + frame.source_row_index.astype(str)).all():
                raise ValueError('Transaction ID mismatch')
            # Labels must be binary and every value used by scoring present.
            if not frame.is_laundering.isin([0, 1]).all() or frame[['amount_usd', 'amount_usd_asof', *FEATURES]].isna().any().any():
                raise ValueError('Invalid labels or missing scoring values')
            seen[indices] = True
            rows += len(frame)
            positives += int(frame.is_laundering.sum())
            # BQ2: partial corridor totals for this batch; summed at the end.
            aggregates.append(corridor_totals(frame))
            # BQ1: score = 50 (ACH) + 20 (above training P99) + 30 (velocity
            # or fan-out); flagged when score >= 60. See baseline.rules.score_baseline.
            scored = score_baseline(frame, thresholds)
            # Append to one Parquet file; the schema is taken from the first
            # batch so later batches must match it.
            table = pa.Table.from_pandas(scored, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(output / 'bq1_scored.parquet', table.schema, compression='snappy')
            writer.write_table(table)
            for period, (start, end) in PERIODS.items():
                summaries[period].add(scored.loc[scored.timestamp.ge(start) & scored.timestamp.lt(end)])
            # Ablation on the validation window only: rebuild each rule
            # component (same weights as score_baseline) and score with
            # subsets of them. Test data is never used to choose rules.
            validation = scored.loc[scored.timestamp.ge('2022-09-07') & scored.timestamp.lt('2022-09-09')]
            ach = validation.payment_format.eq('ACH').astype(int)*50
            # Formats unseen in training get an infinite threshold, so they
            # can never trigger the amount rule.
            amount = validation.amount_usd_asof.gt(validation.payment_format.map(thresholds).fillna(float('inf'))).astype(int)*20
            # Behaviour rule: more than 10 sends in the last hour, or more
            # than 15 distinct receivers in the last 24 hours.
            behaviour = (validation.txn_count_1h.gt(10) | validation.distinct_counterparties_24h.gt(15)).astype(int)*30
            for name, scores in [('Current baseline', ach+amount+behaviour), ('ACH only', ach),
                                 ('ACH + amount', ach+amount), ('ACH + behaviour', ach+behaviour)]:
                variants[name].add(validation.assign(risk_score=scores, flagged=scores.ge(60)))
            print(f'Scored {rows:,} verified transactions', flush=True)
    finally:
        # Always close the writer so the Parquet footer is written, even if a
        # check above failed (the output dir is then discarded by the caller).
        if writer is not None:
            writer.close()
    # Population-level reconciliation: every source row scored once, and the
    # row and positive-label totals equal the independently verified totals.
    if not seen.all() or rows != proof['rows'] or positives != proof['positives']:
        raise ValueError('Source counts differ from verification')
    # Combine per-batch corridor partials. A corridor can appear in several
    # batches, so group again on the same keys and sum.
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    daily = pd.concat(aggregates, ignore_index=True).groupby(keys, observed=True)[['transaction_count', 'volume_usd']].sum().reset_index()
    # Treasury reconciliation: corridor totals must add back to the full row
    # count and the verified total USD (tolerance covers float summing order).
    if int(daily.transaction_count.sum()) != rows or not np.isclose(daily.volume_usd.sum(), proof['amount_usd'], rtol=1e-10, atol=.01):
        raise ValueError('Treasury totals do not reconcile')
    daily.to_parquet(output / 'bq2_daily_corridors.parquet', index=False)
    if not serving_only:
        # Extra analyst reports: daily HHI / top-5 share, and all-time
        # corridor ranking (date dropped from the keys) by USD volume.
        concentration(daily).to_csv(output / 'concentration_daily.csv', index=False)
        daily.groupby(keys[1:])[['transaction_count', 'volume_usd']].sum().sort_values('volume_usd', ascending=False).to_csv(output / 'corridor_rankings.csv')
    aml = {name: summary.view() for name, summary in summaries.items()}
    if not serving_only:
        for name, view in aml.items():
            pd.DataFrame(view['queue'], columns=QUEUE_COLUMNS).to_csv(output / f'review_queue_{name}.csv', index=False)
    # Explainability table for the validation top-100: for each (score,
    # reasons) combination, how many were reviewed, how many were truly
    # laundering and how many were false positives.
    queue = summaries['validation'].queue
    breakdown = queue.groupby(['risk_score', 'risk_reasons']).agg(reviewed=('is_laundering', 'size'), positives=('is_laundering', 'sum')).reset_index() if queue is not None else pd.DataFrame(columns=['risk_score','risk_reasons','reviewed','positives'])
    breakdown['false_positives'] = breakdown.reviewed - breakdown.positives
    diagnostics = dict(period='2022-09-07 to 2022-09-08',
                       variants=[dict(variant=name, **summary.view()['metrics']) for name, summary in variants.items()],
                       top100_reasons=records(breakdown), decision='Exploratory validation comparison; no test-set retuning or automatic promotion.')
    # BQ2 dashboard views: one for the whole period ('all') plus one per day,
    # and a compact daily trend list (volume, HHI, top-5 share).
    daily['txn_date'] = pd.to_datetime(daily.txn_date).dt.strftime('%Y-%m-%d')
    treasury = {'all': treasury_view(daily)}
    trends = []
    for day, group in daily.groupby('txn_date'):
        treasury[day] = treasury_view(group)
        trends.append(dict(day=day, **{key: treasury[day][key] for key in ['transactions','volume_usd','hhi','top5_share']}))
    # Provenance: SHA-256 of each data file so the published dashboard can be
    # traced to exactly these outputs (and the proof file they came from).
    for name in ['bq1_scored.parquet', 'bq2_daily_corridors.parquet']:
        with (output / name).open('rb') as source:
            manifest.append(dict(file=name, sha256=hashlib.file_digest(source, 'sha256').hexdigest()))
    provenance = dict(curated=str(curated), verification_sha256=hashlib.sha256(Path(verification).read_bytes()).hexdigest(),
                      training_p99=thresholds, aml_scope='full', scoring_location='local export from verified AWS Glue output')
    # schema_version=1 is the snapshot format the dashboard.js front end reads.
    snapshot = dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(), aml=aml,
                    treasury=treasury, treasury_daily=trends, diagnostics=diagnostics,
                    cohort_rows=rows, full_rows=rows, aml_scope='full', sources=manifest, provenance=provenance)
    dashboard = output / 'dashboard'
    dashboard.mkdir()
    # allow_nan=False: NaN is not valid JSON and would break the browser
    # parser, so fail here instead of publishing a broken file.
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
