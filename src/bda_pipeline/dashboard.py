"""Reproducible static dashboard snapshots and validation-only rule diagnostics.

Where this sits in the pipeline
-------------------------------
The project dashboard is a static website (``index.html`` + ``dashboard.js``
+ ``dashboard.css``) hosted on S3 behind CloudFront. It has no server or
database: everything it shows comes from one JSON file, ``dashboard.json``.
This module builds that JSON ("snapshot") from:

* a scored transaction table for BQ1 (the AML risk review queue), and
* a daily corridor table for BQ2 (treasury volume and concentration in USD).

Two ways it is used:

1. ``export_dashboard`` is the local entry point, run by
   ``scripts/dashboard/build-dashboard.py --scored ... --corridors ...
   --output ...`` on Parquet files already on disk.
2. The small helpers (``records``, ``ranked``, ``treasury_view``) are reused
   by ``batch.py`` (full-population streaming export), ``model_dashboard.py``
   and ``ingest_compute.py`` in the AWS Glue baseline/export path, so every
   route produces the same JSON shape (``schema_version=1``).

Labels (``is_laundering``) are used only to compute evaluation metrics; the
review queue sorting itself never looks at them.
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .analytics import ranking_metrics
from .splits import LEGACY_SPLITS, eval_names, window_label


def records(frame):
    """Convert a DataFrame to a list of plain-JSON dicts (one per row).

    Going through ``DataFrame.to_json`` (instead of ``to_dict``) turns numpy
    ints/floats and pandas Timestamps into JSON-safe values, with timestamps
    as ISO-8601 strings that the browser can parse.
    """
    return json.loads(frame.to_json(orient='records', date_format='iso'))


def ranked(frame):
    """Return rows in review-queue order: highest risk first.

    Ties on risk_score are broken by earliest ``timestamp`` and then by
    ``source_row_index`` (original CSV row). The stable sort plus this full
    tie-break makes the top-100 queue identical on every run.
    """
    return frame.sort_values(['risk_score', 'timestamp', 'source_row_index'],
                             ascending=[False, True, True], kind='stable')


def diagnose_rules(scored, splits=None):
    """Compare rule variants on the validation window only (an ablation study).

    Args:
        scored: output of ``analytics.score_baseline`` for the whole cohort,
            including the label column and the behaviour features.
        splits: chronological windows (see ``splits.py``); defaults to HI-Small.

    Returns:
        dict with the validation period, metrics for each variant
        ("Current baseline", "ACH only", "ACH + amount", "ACH + behaviour"),
        a reason breakdown of the validation top-100 queue, and a fixed
        decision note.

    Why: it shows which parts of the rule score actually help, while keeping
    the test period untouched, so the reported test metrics are not tuned on
    test data. Nothing is promoted automatically from this comparison.
    """
    splits = splits or LEGACY_SPLITS
    start, end = splits['validation']
    training = scored.loc[scored.timestamp.lt(start)]
    validation = scored.loc[scored.timestamp.ge(start) & scored.timestamp.lt(end)].copy()
    # Re-derive the per-format P99 amount threshold from training rows only.
    thresholds = training.groupby('payment_format').amount_usd_asof.quantile(.99)
    # Rebuild each rule component with the same weights as score_baseline:
    # ACH = 50, large amount = 20, high velocity / fan-out = 30.
    ach = validation.payment_format.eq('ACH').astype(int)*50
    # Formats with no training threshold get +inf, so they never count as large.
    amount = validation.amount_usd_asof.gt(validation.payment_format.map(thresholds).fillna(float('inf'))).astype(int)*20
    behaviour = (validation.txn_count_1h.gt(10) | validation.distinct_counterparties_24h.gt(15)).astype(int)*30
    variants = {'Current baseline': ach+amount+behaviour, 'ACH only': ach,
                'ACH + amount': ach+amount, 'ACH + behaviour': ach+behaviour}
    comparisons = []
    for name, scores in variants.items():
        metrics = ranking_metrics(ranked(validation.assign(risk_score=scores)))
        comparisons.append(dict(variant=name, **metrics))
    # Explainability for the actual queue: group the top 100 by score and
    # reason text, count reviewed rows, true positives and false positives.
    queue = ranked(validation).head(100)
    breakdown = queue.groupby(['risk_score', 'risk_reasons'], dropna=False).agg(
        reviewed=('is_laundering', 'size'), positives=('is_laundering', 'sum')).reset_index()
    breakdown['false_positives'] = breakdown.reviewed - breakdown.positives
    return dict(period=window_label(splits['validation']), variants=comparisons,
                top100_reasons=records(breakdown),
                decision='Exploratory comparison only. Keep current baseline; no test-set retuning or automatic promotion.')


def aml_view(frame):
    """Build the BQ1 dashboard section for one evaluation period.

    Args:
        frame: scored rows for the period (risk_score, risk_reasons, flagged,
            is_laundering, timestamp, queue columns).

    Returns:
        dict with ranking ``metrics`` (precision/recall/lift at 100, average
        precision), number of ``flagged`` rows (score >= 60), the top-100
        ``queue`` an analyst would review, ``daily`` transaction/alert counts
        and a risk-score ``histogram``.
    """
    ordered = ranked(frame)
    metrics = ranking_metrics(ordered)
    # Columns shown in the queue table; the label is intentionally left out.
    queue_columns = ['transaction_id', 'timestamp', 'from_bank', 'from_account', 'to_bank',
                     'to_account', 'payment_format', 'payment_currency', 'amount_usd_asof',
                     'risk_score', 'risk_reasons', 'txn_count_1h', 'distinct_counterparties_24h']
    daily = frame.assign(day=frame.timestamp.dt.strftime('%Y-%m-%d')).groupby('day').agg(
        transactions=('risk_score', 'size'), alerts=('flagged', 'sum')).reset_index()
    histogram = frame.groupby('risk_score').size().rename('transactions').reset_index()
    return dict(metrics=metrics, flagged=int(frame.flagged.sum()), queue=records(ordered.head(100)[queue_columns]),
                daily=records(daily), histogram=records(histogram))


def treasury_view(frame):
    """Build the BQ2 treasury/corridor dashboard section for a set of corridor rows.

    Args:
        frame: daily corridor aggregates with ``from_bank``, ``to_bank``,
            ``payment_currency``, ``receiving_currency``, ``volume_usd`` and
            ``transaction_count`` (one day, or all days).

    Returns:
        dict with total ``transactions`` and ``volume_usd``, concentration
        measures ``hhi`` and ``top5_share``, the 15 largest bank pairs, all
        currency pairs with their share of USD volume, and the number of
        distinct bank pairs.

    Concentration formulas (on directed bank pairs, currencies summed first):
        share_i    = volume_i / total volume
        HHI        = sum(share_i ** 2), from ~0 (spread out) to 1 (one corridor)
        top5_share = sum of the five largest shares
    A high HHI / top-5 share means treasury liquidity depends on few corridors.
    """
    banks = frame.groupby(['from_bank', 'to_bank'], observed=True).agg(
        volume_usd=('volume_usd', 'sum'), transactions=('transaction_count', 'sum')).reset_index()
    currencies = frame.groupby(['payment_currency', 'receiving_currency'], observed=True).agg(
        volume_usd=('volume_usd', 'sum'), transactions=('transaction_count', 'sum')).reset_index()
    total = float(banks.volume_usd.sum())
    # Guard against division by zero on an empty/zero-volume selection; HHI
    # and top-5 share are then reported as None (null in JSON).
    shares = banks.volume_usd / total if total else banks.volume_usd * 0
    banks['share'] = shares
    currencies['share'] = currencies.volume_usd / total if total else 0
    return dict(transactions=int(frame.transaction_count.sum()), volume_usd=total,
                hhi=float((shares**2).sum()) if total else None,
                top5_share=float(shares.nlargest(5).sum()) if total else None,
                banks=records(banks.sort_values('volume_usd', ascending=False).head(15)),
                currencies=records(currencies.sort_values('volume_usd', ascending=False)),
                bank_pairs=len(banks))


def build_snapshot(scored, corridors, splits=None):
    """Assemble the full ``dashboard.json`` structure from in-memory tables.

    Args:
        scored: scored transactions (from ``analytics.score_baseline``).
        corridors: daily corridor totals (from ``analytics.corridor_totals``).
        splits: chronological windows (see ``splits.py``); defaults to HI-Small.

    Returns:
        dict with ``aml`` (one view per evaluation window), ``treasury`` (an
        ``'all'`` view plus one per day), ``treasury_daily`` trend rows,
        ``diagnostics`` and row counts.

    Raises:
        ValueError: if transaction IDs repeat, labels are not 0/1, or corridor
        measurements are missing. These checks stop a bad snapshot from being
        published rather than showing misleading numbers.
    """
    if scored.transaction_id.duplicated().any():
        raise ValueError('Duplicate transaction identities')
    if not scored.is_laundering.isin([0, 1]).all():
        raise ValueError('Labels must be binary')
    if corridors[['volume_usd', 'transaction_count']].isna().any().any():
        raise ValueError('Missing corridor measurements')
    splits = splits or LEGACY_SPLITS
    aml = {}
    for name in eval_names(splits):
        start, end = splits[name]
        aml[name] = aml_view(scored.loc[scored.timestamp.ge(start) & scored.timestamp.lt(end)])
    corridors = corridors.copy()
    # Normalise dates to 'YYYY-MM-DD' strings so they work as JSON keys and
    # as the day selector values in the front end.
    corridors['txn_date'] = pd.to_datetime(corridors.txn_date).dt.strftime('%Y-%m-%d')
    treasury = {'all': treasury_view(corridors)}
    daily = []
    for day, group in corridors.groupby('txn_date', observed=True):
        treasury[day] = treasury_view(group)
        daily.append(dict(day=day, **{key: treasury[day][key] for key in ['transactions','volume_usd','hhi','top5_share']}))
    diagnostics = diagnose_rules(scored, splits)
    # cohort_rows = rows scored for BQ1 here; full_rows = all transactions
    # covered by the BQ2 corridors (they differ if BQ1 used a sample cohort).
    return dict(schema_version=1, generated_at=datetime.now(timezone.utc).isoformat(),
                aml=aml, treasury=treasury, treasury_daily=daily, diagnostics=diagnostics,
                cohort_rows=len(scored), full_rows=treasury['all']['transactions'], splits=splits)


def export_dashboard(scored_path, corridors_path, output):
    """Read Parquet inputs, build the snapshot and write it to ``output``.

    Args:
        scored_path: Parquet file of scored transactions.
        corridors_path: Parquet file of daily corridor totals.
        output: directory to write into (created if needed).

    Writes ``dashboard.json`` (compact) and ``validation-diagnostics.json``
    (pretty-printed, for humans). The SHA-256 of each input is stored under
    ``sources`` so a reader can tell exactly which files the numbers came from.

    Returns:
        the snapshot dict.
    """
    snapshot = build_snapshot(pd.read_parquet(scored_path), pd.read_parquet(corridors_path))
    snapshot['sources'] = [{'file': Path(path).name, 'sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest()}
                           for path in [scored_path, corridors_path]]
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # allow_nan=False makes json.dumps raise instead of writing invalid JSON.
    (output / 'dashboard.json').write_text(json.dumps(snapshot, allow_nan=False), encoding='utf-8')
    (output / 'validation-diagnostics.json').write_text(json.dumps(snapshot['diagnostics'], indent=2, allow_nan=False), encoding='utf-8')
    return snapshot
