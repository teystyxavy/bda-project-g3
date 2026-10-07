"""Run the local smoke version of the batch transformation.

Where this sits in the pipeline
-------------------------------
The cloud pipeline's "curate" step is the Glue PySpark job
``jobs/curate_transactions.py``: it reads the raw Kaggle CSVs from S3, joins
account metadata and FX rates, converts amounts to USD and writes
date-partitioned Parquet to the curated zone. This script runs the *pandas
equivalent* of that step (``bda_pipeline.transform``) on a small sample
(default the first 1,000 rows) on a laptop - a quick "smoke test" that the
transformation logic and inputs work before paying for Glue.

Reads / writes
--------------
* Reads: ``dataset/HI-Small_Trans.csv``, ``dataset/HI-Small_accounts.csv``
  and an FX CSV (e.g. from ``scripts/ingestion/fetch-fx-rates.py --rows 1000``).
* Writes: Hive-style Parquet ``<output>/txn_date=YYYY-MM-DD/*.parquet``
  (default ``output/curated/transactions``), the same layout as the curated
  S3 zone.

Usage
-----
    python scripts/local/run-local-pipeline.py --fx-rates dataset/fx_rates.csv --rows 1000
"""

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


# Repository root, so default paths work from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    """Define and parse the command-line options.

    Options:
        --transactions / --accounts: raw Kaggle CSVs (default under dataset/).
        --fx-rates: required daily FX CSV (txn_date, currency, usd_rate, ...).
        --rows: number of transactions to read from the top of the file.
        --output: destination folder for the partitioned Parquet.

    Returns:
        The parsed ``argparse.Namespace``.
    """
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
    """Read the sample, curate it and write partitioned Parquet.

    Raises:
        ValueError: (from ``bda_pipeline.transform``) on schema, validation or
            FX-coverage problems - no partial output is written in that case.
    """
    args = parse_args()
    # Columns are read by position (the raw file has two 'Account' headers),
    # and each row keeps its original file position as source_row_index.
    transactions = read_transactions(args.transactions, nrows=args.rows)
    accounts = read_accounts(args.accounts)
    fx_rates = pd.read_csv(args.fx_rates)
    # Validate types/values, add txn_date, self-transfer and currency-mismatch
    # flags, attach account metadata and convert amount_paid to amount_usd.
    curated = build_curated_transactions(transactions, accounts, fx_rates)
    # One folder per txn_date, so Athena/pyarrow can skip days when filtering.
    write_partitioned_parquet(curated, args.output)
    print(f"Wrote {len(curated):,} curated transactions to {args.output}")


if __name__ == "__main__":
    main()
