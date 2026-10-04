"""Run the local smoke version of the batch transformation."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from bda_pipeline.transform import (
    build_curated_transactions,
    read_accounts,
    read_transactions,
    write_partitioned_parquet,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transactions",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "HI-Small_Trans.csv",
    )
    parser.add_argument(
        "--accounts",
        type=Path,
        default=PROJECT_ROOT / "dataset" / "HI-Small_accounts.csv",
    )
    parser.add_argument(
        "--fx-rates",
        type=Path,
        required=True,
        help="CSV with txn_date,currency,usd_rate columns",
    )
    parser.add_argument("--rows", type=int, default=1_000)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "output" / "curated" / "transactions",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    transactions = read_transactions(args.transactions, nrows=args.rows)
    accounts = read_accounts(args.accounts)
    fx_rates = pd.read_csv(args.fx_rates)
    curated = build_curated_transactions(transactions, accounts, fx_rates)
    write_partitioned_parquet(curated, args.output)
    print(f"Wrote {len(curated):,} curated transactions to {args.output}")


if __name__ == "__main__":
    main()
