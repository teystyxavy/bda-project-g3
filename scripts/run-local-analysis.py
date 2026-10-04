"""Validate all rows, produce full-data BQ2 and sender-cohort BQ1 outputs."""
import argparse
import json
from pathlib import Path

import pandas as pd

from bda_pipeline.analytics import add_behaviour, bank_key, concentration, corridor_totals, evaluate_baseline
from bda_pipeline.transform import TRANSACTION_COLUMNS, IDENTIFIER_DTYPES, build_curated_transactions, read_accounts


def main():
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
    fx = pd.read_csv(args.fx_rates)
    prior_fx = fx[['txn_date', 'currency', 'usd_rate']].copy()
    prior_fx['txn_date'] = (pd.to_datetime(prior_fx.txn_date) + pd.Timedelta(days=1)).dt.date
    prior_fx = prior_fx.rename(columns={'currency': 'payment_currency', 'usd_rate': 'prior_usd_rate'})
    cohorts, aggregates = [], []
    count = 0
    report = dict(rows=0, missing_from_metadata=0, missing_to_metadata=0, positives=0)
    for raw in pd.read_csv(args.dataset / 'HI-Small_Trans.csv', header=0,
                           names=TRANSACTION_COLUMNS, dtype=IDENTIFIER_DTYPES, chunksize=250_000):
        raw.insert(0, 'source_row_index', range(count, count+len(raw)))
        curated = build_curated_transactions(raw, accounts, fx)
        if len(curated) != len(raw) or curated.amount_usd.isna().any():
            raise ValueError('Transformation row count or FX coverage failure')
        report['missing_from_metadata'] += int(curated.from_entity_id.isna().sum())
        report['missing_to_metadata'] += int(curated.to_entity_id.isna().sum())
        report['positives'] += int(curated.is_laundering.sum())
        aggregates.append(corridor_totals(curated))
        keys = pd.DataFrame({'bank': bank_key(curated.from_bank), 'account': curated.from_account})
        selected = pd.util.hash_pandas_object(keys, index=False).mod(args.sender_modulus).eq(0)
        cohorts.append(curated.loc[selected].copy())
        count += len(raw)
        print(f'Validated {count:,} transactions', flush=True)
    report['rows'] = count
    cohort = pd.concat(cohorts, ignore_index=True)
    cohort = cohort.merge(prior_fx, on=['txn_date', 'payment_currency'], how='left', validate='many_to_one')
    cohort.loc[cohort.payment_currency.eq('US Dollar'), 'prior_usd_rate'] = 1.0
    if cohort.prior_usd_rate.isna().any():
        raise ValueError('Prior-day FX coverage missing; fetch with --lookback-days 1')
    cohort['amount_usd_asof'] = cohort.amount_paid * cohort.prior_usd_rate
    cohort['transaction_id'] = 'HI-Small:' + cohort.source_row_index.astype(str)
    print(f'Computing complete sender histories for {len(cohort):,} cohort rows', flush=True)
    cohort = add_behaviour(cohort)
    ranked, thresholds, metrics = evaluate_baseline(cohort)
    daily = pd.concat(aggregates, ignore_index=True)
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    daily = daily.groupby(keys)[['transaction_count', 'volume_usd']].sum().reset_index()
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
    args.output.mkdir(parents=True)
    ranked.to_parquet(args.output / 'bq1_scored.parquet', index=False)
    ranked.head(100).drop(columns=['is_laundering']).to_csv(args.output / 'review_queue.csv', index=False)
    daily.to_parquet(args.output / 'bq2_daily_corridors.parquet', index=False)
    daily.groupby(keys[1:])[['transaction_count', 'volume_usd']].sum().sort_values('volume_usd', ascending=False).to_csv(args.output / 'corridor_rankings.csv')
    concentration(daily).to_csv(args.output / 'concentration_daily.csv', index=False)
    (args.output / 'run_report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
