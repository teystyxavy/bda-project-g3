"""Validate all rows, produce full-data BQ2 and sender-cohort BQ1 outputs.

Where this sits in the pipeline
-------------------------------
A laptop-sized version of the full analysis, used to develop and sanity-check
both business questions before (and alongside) the AWS pipeline:

* BQ2 (treasury corridors) on the *full* HI-Small file: every transaction is
  curated (same pandas logic as the Glue curate job), converted to USD at the
  same-day historical rate, and summed per day and corridor
  (from_bank -> to_bank, payment -> receiving currency). Daily concentration
  is measured with the Herfindahl-Hirschman Index (HHI = sum of squared volume
  shares of each bank pair) and the top-5 share.
* BQ1 (AML review queue) on a *sender cohort*: computing exact behaviour
  windows for all ~5 million rows is slow in pandas, so only senders whose
  hashed (bank, account) key is 0 modulo ``--sender-modulus`` (default 20,
  i.e. about 1 in 20 senders) are kept - but each kept sender keeps their
  *complete* history, so their velocity/fan-out features are exact. The cohort
  is then scored by the rule-based baseline and evaluated chronologically.

Reads
-----
``<dataset>/HI-Small_Trans.csv``, ``<dataset>/HI-Small_accounts.csv`` and the
FX CSV from ``scripts/ingestion/fetch-fx-rates.py --lookback-days 1`` (the extra
day is needed for the prior-day rate on Sep 1).

Writes (new ``--output`` folder)
--------------------------------
* ``bq1_scored.parquet`` - scored, ranked cohort; ``review_queue.csv`` - top 100
  without the label column.
* ``bq2_daily_corridors.parquet``; ``corridor_rankings.csv`` (corridors by total
  USD volume); ``concentration_daily.csv`` (HHI and top-5 share per day).
* ``run_report.json`` - row counts, missing metadata, positives, thresholds,
  metrics, scope and caveats.

Usage
-----
    python scripts/local/run-local-analysis.py \
        --fx-rates dataset/fx_rates.csv --output output/local-analysis-1
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from bda_pipeline.baseline.rules import evaluate_baseline
from bda_pipeline.features.behaviour import add_behaviour, bank_key
from bda_pipeline.treasury.corridors import concentration, corridor_totals
from bda_pipeline.ingestion.transform import TRANSACTION_COLUMNS, IDENTIFIER_DTYPES, build_curated_transactions, read_accounts


def main():
    """Stream, validate and curate every transaction; write BQ1 and BQ2 outputs.

    Memory approach: the CSV is read in chunks of 250,000 rows. Each chunk is
    reduced immediately to (a) its small corridor aggregate and (b) the rows
    of cohort senders, so the full curated table is never held in memory.

    Raises:
        ValueError: if any chunk loses rows or lacks FX coverage, prior-day FX
            is missing for the cohort, or corridor totals do not reconcile with
            the number of rows read.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path('dataset'))
    parser.add_argument('--fx-rates', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing directories are rejected')
    parser.add_argument('--sender-modulus', type=int, default=20, help='Keep hash(sender) modulo N = 0; 1 includes all senders')
    args = parser.parse_args()
    if args.sender_modulus < 1:
        parser.error('sender-modulus must be positive')
    if args.output.exists():
        parser.error('Choose a new output directory to avoid mixing runs')
    accounts = read_accounts(args.dataset / 'HI-Small_accounts.csv')
    # Same-day FX (used for amount_usd / BQ2) comes straight from the file.
    fx = pd.read_csv(args.fx_rates)
    # Prior-day FX for BQ1: shift each rate's date forward by one day, so a
    # transaction on day D joins the rate of day D-1. The model/rules then only
    # use information that was available before the transaction (no look-ahead).
    prior_fx = fx[['txn_date', 'currency', 'usd_rate']].copy()
    prior_fx['txn_date'] = (pd.to_datetime(prior_fx.txn_date) + pd.Timedelta(days=1)).dt.date
    prior_fx = prior_fx.rename(columns={'currency': 'payment_currency', 'usd_rate': 'prior_usd_rate'})
    # count: rows processed so far (also the next global source_row_index).
    cohorts, aggregates = [], []
    count = 0
    report = dict(rows=0, missing_from_metadata=0, missing_to_metadata=0, positives=0)
    # Columns by position (the raw header has two 'Account' columns); IDs as
    # strings so leading zeroes survive.
    for raw in pd.read_csv(args.dataset / 'HI-Small_Trans.csv', header=0,
                           names=TRANSACTION_COLUMNS, dtype=IDENTIFIER_DTYPES, chunksize=250_000):
        # Global 0-based file position: stable transaction identity across chunks.
        raw.insert(0, 'source_row_index', range(count, count+len(raw)))
        curated = build_curated_transactions(raw, accounts, fx)
        # Every row must survive curation and get a USD amount (full FX coverage).
        if len(curated) != len(raw) or curated.amount_usd.isna().any():
            raise ValueError('Transformation row count or FX coverage failure')
        # Data-quality counters: accounts with no metadata, and labelled positives.
        report['missing_from_metadata'] += int(curated.from_entity_id.isna().sum())
        report['missing_to_metadata'] += int(curated.to_entity_id.isna().sum())
        report['positives'] += int(curated.is_laundering.sum())
        # BQ2: aggregate this chunk now; partial sums are re-summed after the loop.
        aggregates.append(corridor_totals(curated))
        # BQ1 cohort selection: deterministic hash of the normalised sender
        # (bank, account). Same sender -> same hash in every chunk, so a selected
        # sender's whole history is kept. Modulus 1 keeps everyone.
        keys = pd.DataFrame({'bank': bank_key(curated.from_bank), 'account': curated.from_account})
        selected = pd.util.hash_pandas_object(keys, index=False).mod(args.sender_modulus).eq(0)
        cohorts.append(curated.loc[selected].copy())
        count += len(raw)
        print(f'Validated {count:,} transactions', flush=True)
    report['rows'] = count
    cohort = pd.concat(cohorts, ignore_index=True)
    # Attach the prior-day rate; many_to_one asserts one rate per (day, currency).
    # US Dollar needs no rate (always 1.0).
    cohort = cohort.merge(prior_fx, on=['txn_date', 'payment_currency'], how='left', validate='many_to_one')
    cohort.loc[cohort.payment_currency.eq('US Dollar'), 'prior_usd_rate'] = 1.0
    if cohort.prior_usd_rate.isna().any():
        raise ValueError('Prior-day FX coverage missing; fetch with --lookback-days 1')
    # amount_usd_asof: USD value using only the rate known before the day.
    cohort['amount_usd_asof'] = cohort.amount_paid * cohort.prior_usd_rate
    cohort['transaction_id'] = 'HI-Small:' + cohort.source_row_index.astype(str)
    print(f'Computing complete sender histories for {len(cohort):,} cohort rows', flush=True)
    # Velocity (txn counts in prior 1h/24h/7d) and fan-out (distinct
    # counterparties in prior 24h/7d) per sender, strictly before each txn.
    cohort = add_behaviour(cohort)
    # Fit P99 amount thresholds on Sep 1-6, score with the rules, and measure
    # ranking quality on validation (Sep 7-8), test (Sep 9-10) and the tail.
    ranked, thresholds, metrics = evaluate_baseline(cohort)
    # Combine per-chunk aggregates: a corridor-day can appear in several chunks.
    daily = pd.concat(aggregates, ignore_index=True)
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    daily = daily.groupby(keys)[['transaction_count', 'volume_usd']].sum().reset_index()
    # Reconciliation: corridor counts must add up to exactly the rows read.
    if int(daily.transaction_count.sum()) != count:
        raise ValueError('Corridor totals do not reconcile')
    report.update(cohort_rows=len(cohort), cohort_currencies=int(cohort.payment_currency.nunique()),
                  cohort_dates=int(cohort.txn_date.nunique()), sender_modulus=args.sender_modulus,
                  metrics=metrics, training_p99=thresholds,
                  scope='BQ2: full HI-Small; BQ1: complete histories of hash-selected senders',
                  caveats=['Behaviour weights remain hypotheses; existing EDA inspected the full dataset.',
                           'Cold-start history before Sept 1 is unavailable.',
                           'Equal scores break ties by timestamp then source row; metrics depend on this policy.',
                           'FX uses prior calendar day for BQ1, same-day historical valuation for BQ2.'])
    # Outputs are written only after every check passed (no partial results).
    args.output.mkdir(parents=True)
    ranked.to_parquet(args.output / 'bq1_scored.parquet', index=False)
    ranked.head(100).drop(columns=['is_laundering']).to_csv(args.output / 'review_queue.csv', index=False)
    daily.to_parquet(args.output / 'bq2_daily_corridors.parquet', index=False)
    # Corridor ranking over the whole period (date dropped from the keys).
    daily.groupby(keys[1:])[['transaction_count', 'volume_usd']].sum().sort_values('volume_usd', ascending=False).to_csv(args.output / 'corridor_rankings.csv')
    concentration(daily).to_csv(args.output / 'concentration_daily.csv', index=False)
    (args.output / 'run_report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
