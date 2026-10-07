"""Fetch the historical USD rates required by an IBM AML transaction file.

Where this sits in the pipeline
-------------------------------
BQ2 (corridor volume and concentration in USD) needs every transaction amount
converted to US dollars, but the Kaggle IBM AML dataset has no exchange rates.
This stand-alone CLI scans a transaction CSV to find which currencies and which
calendar dates occur, then downloads one historical daily USD rate per
(date, currency) using ``bda_pipeline.fx`` (Frankfurter/ECB reference rates,
with Yahoo Finance daily closes for BTC, RUB and SAR, which Frankfurter lacks
for September 2022).

The normal pipeline (``ingest.py`` -> ``acquisition.prepare_inputs``) builds FX
itself; this script is for producing a local ``fx_rates.csv`` for offline or
local runs (e.g. passed to ``ingest.py prepare --fx-file`` or used by the local
analysis scripts in ``scripts/local``).

Reads / writes
--------------
* Reads: the transactions CSV (default ``dataset/HI-Small_Trans.csv``).
* Calls: public FX HTTP APIs (network access required).
* Writes: CSV with columns ``txn_date, currency, iso_code, usd_rate,
  rate_date, source`` (default ``dataset/fx_rates.csv``).

Usage
-----
    python scripts/ingestion/fetch-fx-rates.py --rows 1000
    python scripts/ingestion/fetch-fx-rates.py --lookback-days 1
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

from bda_pipeline.fx import build_daily_rates, discover_requirements, write_rates_csv


# Repository root (this file is scripts/ingestion/fetch-fx-rates.py), so the
# default paths work no matter which directory the script is run from.
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    """Define and parse the command-line options.

    Options:
        --lookback-days: extra calendar days to fetch *before* the first
            transaction date. The model uses the previous day's rate
            (``amount_usd_asof``) so that features never use a rate published
            after the transaction; a 1-day lookback makes that rate available
            for the first day too.
        --transactions: input IBM AML transaction CSV.
        --output: destination CSV for the rates.
        --rows: only scan the first N rows (1000 for the quick smoke run).

    Returns:
        The parsed ``argparse.Namespace``.
    """
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
    """Discover required dates/currencies, fetch the rates and write the CSV.

    Side effects:
        Network calls to the FX APIs and one CSV file written to ``--output``.

    Raises:
        ValueError: if ``--lookback-days`` is negative.
    """
    args = parse_args()
    # Stream the CSV in chunks to find its min/max transaction date and the set
    # of "Payment Currency" values (unknown currency names raise ValueError).
    start_date, end_date, currencies = discover_requirements(
        args.transactions, nrows=args.rows
    )
    if args.lookback_days < 0:
        raise ValueError('lookback-days must be non-negative')
    # Widen the window backwards for the causal (previous-day) FX feature.
    start_date -= timedelta(days=args.lookback_days)
    # One row per (calendar day, currency); weekends/holidays are filled with
    # the latest earlier published rate, recorded in rate_date.
    rates = build_daily_rates(currencies, start_date, end_date)
    write_rates_csv(rates, args.output)
    print(
        f"Wrote {len(rates):,} rates for {len(currencies)} currencies "
        f"({start_date} to {end_date}) to {args.output}"
    )


if __name__ == "__main__":
    main()
