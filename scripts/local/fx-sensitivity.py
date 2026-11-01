"""Check whether BQ2 corridor rankings depend on the FX rates used to value them.

Where this sits in the pipeline
-------------------------------
A read-only robustness check on a finished BQ2 run. The pipeline values every
payment at the historical same-day rate (``amount_usd = amount_paid *
usd_rate``). The IBM dataset itself converts currencies at fixed, roughly 2020
rates that differ from September 2022 rates by up to ~40% (Bitcoin). This
script re-values the run's daily corridor totals at the dataset's own rates and
compares directed bank-pair rankings, HHI and top-5 share under both.

Because conversion is linear per (day, payment currency), the native amount of
each corridor-day row is recovered exactly as ``volume_usd / usd_rate`` and
re-converted; no transaction file of the analysed run is needed.

The dataset's rates are derived from its cross-currency rows (one side US
Dollar) as volume-weighted ``native / USD``. Every currency's rate is fixed
up to rounding, so any HI/LI transaction file from the same generator works.

Reads
-----
* ``--corridors``: the run's ``bq2_daily_corridors.parquet``
  (``pipeline/runs/<run_id>/baseline/baseline/`` in S3).
* ``--fx``: the run's historical FX table (``pipeline/runs/<run_id>/raw/fx.csv``).
* ``--transactions``: a raw IBM transaction CSV, e.g. ``dataset/HI-Small_Trans.csv``.

Writes (new ``--output`` folder)
--------------------------------
* ``dataset_rates.csv`` - the dataset's implied rate per currency.
* ``bank_pair_rankings.csv`` - all-period volume, share and rank per bank pair
  under both valuations.
* ``daily_comparison.csv`` - per day: top-15 overlap, same #1 pair, HHI under both.
* ``summary.json`` - input hashes, totals, overlaps, concentration and rate gaps.

Usage
-----
    aws s3 cp s3://<bucket>/pipeline/runs/<run_id>/baseline/baseline/bq2_daily_corridors.parquet output/fx/
    aws s3 cp s3://<bucket>/pipeline/runs/<run_id>/raw/fx.csv output/fx/
    python scripts/local/fx-sensitivity.py --corridors output/fx/bq2_daily_corridors.parquet \
        --fx output/fx/fx.csv --transactions dataset/HI-Small_Trans.csv --output output/fx-sensitivity-1
"""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

USD = 'US Dollar'
PAIR = ['from_bank', 'to_bank']
VALUES = {'historical': 'volume_usd', 'dataset': 'volume_dataset'}


def sha256(path):
    """Hex SHA-256 of a file, read in 1 MiB blocks."""
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def dataset_rates(path):
    """USD per unit implied by the dataset's own conversions, per currency.

    Uses rows whose currencies differ and one side is US Dollar; summing both
    sides before dividing keeps rounding on tiny amounts (Bitcoin) from
    dominating.
    """
    # Columns by position: the raw header has two 'Account' columns.
    names = ['amount_received', 'receiving_currency', 'amount_paid', 'payment_currency']
    raw = pd.read_csv(path, header=0, usecols=[5, 6, 7, 8], names=names)
    raw = raw[raw.receiving_currency.ne(raw.payment_currency)]
    paid_usd = raw[raw.payment_currency.eq(USD)]
    received_usd = raw[raw.receiving_currency.eq(USD)]
    sides = pd.concat([
        pd.DataFrame({'currency': paid_usd.receiving_currency, 'native': paid_usd.amount_received, 'usd': paid_usd.amount_paid}),
        pd.DataFrame({'currency': received_usd.payment_currency, 'native': received_usd.amount_paid, 'usd': received_usd.amount_received})])
    totals = sides.groupby('currency')[['native', 'usd']].sum()
    rates = (totals.usd / totals.native).rename('usd_per_unit')
    rates[USD] = 1.0
    return rates


def revalue(corridors, fx, rates):
    """Add ``volume_dataset``: each corridor-day's native amount at the dataset's rate.

    Raises:
        ValueError: if a payment currency lacks a historical or dataset rate.
    """
    fx = fx[['txn_date', 'currency', 'usd_rate']].rename(columns={'currency': 'payment_currency'})
    fx['txn_date'] = pd.to_datetime(fx.txn_date).dt.date
    frame = corridors.assign(txn_date=pd.to_datetime(corridors.txn_date).dt.date)
    frame = frame.merge(fx, on=['txn_date', 'payment_currency'], how='left', validate='many_to_one')
    frame.loc[frame.payment_currency.eq(USD), 'usd_rate'] = 1.0
    frame['volume_dataset'] = frame.volume_usd / frame.usd_rate * frame.payment_currency.map(rates)
    if frame.volume_dataset.isna().any():
        raise ValueError('Missing historical or dataset rate for: '
                         + ', '.join(sorted(frame.loc[frame.volume_dataset.isna(), 'payment_currency'].unique())))
    return frame


def concentration(pairs, column):
    """(HHI, top-5 share) of directed bank pairs for one valuation."""
    shares = pairs[column] / pairs[column].sum()
    return float((shares**2).sum()), float(shares.nlargest(5).sum())


def top(pairs, column, k):
    """Set of the k largest bank pairs (as index tuples) for one valuation."""
    return set(pairs[column].nlargest(k).index)


def main():
    """Revalue the run's corridors, compare rankings and write the outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corridors', type=Path, required=True)
    parser.add_argument('--fx', type=Path, required=True)
    parser.add_argument('--transactions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing directories are rejected')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output directory to avoid mixing runs')
    rates = dataset_rates(args.transactions)
    frame = revalue(pd.read_parquet(args.corridors), pd.read_csv(args.fx), rates)

    pairs = frame.groupby(PAIR)[['transaction_count', *VALUES.values()]].sum()
    for name, column in VALUES.items():
        pairs[f'share_{name}'] = pairs[column] / pairs[column].sum()
        pairs[f'rank_{name}'] = pairs[column].rank(ascending=False, method='first').astype(int)
    pairs = pairs.sort_values('rank_historical')

    daily = []
    for day, group in frame.groupby('txn_date'):
        day_pairs = group.groupby(PAIR)[list(VALUES.values())].sum()
        hhi = {name: concentration(day_pairs, column)[0] for name, column in VALUES.items()}
        # Days with fewer than 15 pairs have no top 15 to compare (overlap left empty).
        overlap = len(top(day_pairs, 'volume_usd', 15) & top(day_pairs, 'volume_dataset', 15)) if len(day_pairs) >= 15 else None
        daily.append(dict(txn_date=str(day), bank_pairs=len(day_pairs), top15_overlap=overlap,
                          same_top_pair=bool(day_pairs.volume_usd.idxmax() == day_pairs.volume_dataset.idxmax()),
                          hhi_historical=hhi['historical'], hhi_dataset=hhi['dataset']))
    daily = pd.DataFrame(daily)

    currencies = frame.groupby('payment_currency')[list(VALUES.values())].sum()
    summary = dict(
        inputs={str(path.name): sha256(path) for path in (args.corridors, args.fx, args.transactions)},
        transactions=int(frame.transaction_count.sum()), bank_pairs=len(pairs),
        volume={name: float(frame[column].sum()) for name, column in VALUES.items()},
        concentration={name: dict(zip(['hhi', 'top5_share'], concentration(pairs, column))) for name, column in VALUES.items()},
        top_k_overlap={k: len(top(pairs, 'volume_usd', k) & top(pairs, 'volume_dataset', k)) for k in (5, 10, 15, 30)},
        top5={name: ['->'.join(pair) for pair in pairs[column].nlargest(5).index] for name, column in VALUES.items()},
        daily=dict(days=len(daily), top15_days=int(daily.top15_overlap.notna().sum()), min_top15_overlap=int(daily.top15_overlap.min()),
                   mean_top15_overlap=float(daily.top15_overlap.mean()), same_top_pair_days=int(daily.same_top_pair.sum())),
        # Dataset valuation relative to historical, per payment currency, volume-weighted.
        rate_gap=(currencies.volume_dataset / currencies.volume_usd - 1).round(4).to_dict(),
        historical_share=(currencies.volume_usd / currencies.volume_usd.sum()).round(4).to_dict(),
        dataset_rates_source=args.transactions.name)

    # Outputs are written only after every step succeeded (no partial results).
    args.output.mkdir(parents=True)
    rates.rename_axis('currency').to_csv(args.output / 'dataset_rates.csv')
    pairs.to_csv(args.output / 'bank_pair_rankings.csv')
    daily.to_csv(args.output / 'daily_comparison.csv', index=False)
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ['transactions', 'volume', 'concentration', 'top_k_overlap', 'top5', 'daily']}, indent=2))


if __name__ == '__main__':
    main()
