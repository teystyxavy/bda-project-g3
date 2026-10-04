"""Fetch the historical USD rates required by an IBM AML transaction file."""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

from bda_pipeline.fx import build_daily_rates, discover_requirements, write_rates_csv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--lookback-days', type=int, default=0,
                        help='Include preceding calendar days for causal FX features')
    parser.add_argument(
        "--transactions",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "HI-Small_Trans.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "fx_rates.csv",
    )
    parser.add_argument(
        "--rows",
        type=int,
        default=None,
        help="Inspect only the first N transaction rows (use 1000 for the smoke run)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start_date, end_date, currencies = discover_requirements(
        args.transactions, nrows=args.rows
    )
    if args.lookback_days < 0:
        raise ValueError('lookback-days must be non-negative')
    start_date -= timedelta(days=args.lookback_days)
    rates = build_daily_rates(currencies, start_date, end_date)
    write_rates_csv(rates, args.output)
    print(
        f"Wrote {len(rates):,} rates for {len(currencies)} currencies "
        f"({start_date} to {end_date}) to {args.output}"
    )


if __name__ == "__main__":
    main()
