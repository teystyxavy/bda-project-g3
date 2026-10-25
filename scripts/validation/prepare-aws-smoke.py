"""Prepare a bounded raw sample and a local reference without contacting AWS.

Purpose
-------
Creates a small "smoke test" fixture for the Glue curation job. It takes the
first ``--rows`` (default 1,000) transactions of the Kaggle HI-Small CSV,
the matching account metadata and the FX rate table, and also computes the
*expected* curated output with the trusted local pandas implementation
(``bda_pipeline.ingestion.transform``). After the sample is uploaded to S3 raw and the
Glue job runs on it, ``verify-aws-smoke.py`` compares the Glue output with
this expected Parquet. The same fixture is used by ``test-glue-local.ps1``
inside the AWS Glue Docker image.

Outputs (in ``--output``, default ``output/aws-smoke``; must be new)
    ``transactions.csv`` - header + first N raw rows, copied byte-faithfully
        via the csv module (no re-formatting of values);
    ``accounts.csv``     - only accounts that appear in the sample, with the
        original Kaggle column headers restored;
    ``fx.csv``           - the full FX table (small; dates are needed for both
        same-day and prior-day lookups);
    ``expected.parquet`` - expected curated rows including ``amount_usd``
        (same-day FX, used by treasury/BQ2) and ``amount_usd_asof`` (prior-day
        FX, used by the AML model/BQ1 so no same-day information leaks);
    ``expected.json``    - row count, USD totals and positive-label count.

Scope: "first rows only" - this validates joins/conversions, not behaviour
windows (which need complete sender histories) or model quality.

Usage::

    python scripts/validation/prepare-aws-smoke.py [--rows 1000] [--output output/aws-smoke]
"""

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

# Local reference implementation of the curation logic (pandas), used as the
# "known good" answer the Glue PySpark job must reproduce.
from bda_pipeline.ingestion.transform import build_curated_transactions, read_accounts, read_transactions


def prepare(transactions, accounts, fx, output, rows=1000):
    """Build the sample files and expected curated reference.

    Args:
        transactions: Path to the raw Kaggle transactions CSV.
        accounts: Path to the Kaggle accounts CSV.
        fx: Path to the historical FX rates CSV.
        output: fresh directory to create for the fixture.
        rows: number of leading transactions to include (must be > 0).

    Returns:
        dict summary (also written to ``expected.json``).

    Raises:
        ValueError: non-positive ``rows`` or a sample account with no metadata.
        FileExistsError: ``output`` already exists.
    """
    if rows <= 0:
        raise ValueError("rows must be positive")
    if output.exists():
        raise FileExistsError(f"Use a fresh output directory: {output}")
    # Parse the first N transactions with the same reader the pipeline uses.
    selected = read_transactions(transactions, nrows=rows)
    metadata = read_accounts(accounts)
    rates = pd.read_csv(fx)
    # Expected curated output (account joins + same-day USD conversion).
    reference = build_curated_transactions(selected, metadata, rates)
    # Every sender and receiver must match an account; otherwise the sample
    # would not exercise the join correctly.
    for side in ["from", "to"]:
        if reference[f"{side}_entity_id"].isna().any():
            raise ValueError("Sample has unmatched account metadata")
    # Prior-day FX: shift timestamps back one day, re-run the conversion and take
    # its amount_usd as amount_usd_asof (the AML-safe amount).
    prior = selected.copy()
    prior["timestamp"] = pd.to_datetime(prior.timestamp) - pd.Timedelta(days=1)
    reference["amount_usd_asof"] = build_curated_transactions(prior, metadata, rates).amount_usd
    output.mkdir(parents=True)
    # Copy the raw CSV text for the first N rows (utf-8-sig strips a BOM if present).
    with transactions.open(newline="", encoding="utf-8-sig") as source, (output / "transactions.csv").open("w", newline="", encoding="utf-8") as target:
        reader, writer = csv.reader(source), csv.writer(target)
        writer.writerow(next(reader))
        for index, row in enumerate(reader):
            if index >= rows:
                break
            writer.writerow(row)
    # Normalise bank ids by stripping leading zeros ('020' == '20'); an all-zero
    # id becomes '0'. This mirrors how the pipeline joins banks.
    normalize = lambda values: values.astype("string").str.lstrip("0").replace("", "0")
    # Collect every (bank, account) pair used as sender or receiver in the sample.
    selected_keys = set()
    for side in ["from", "to"]:
        selected_keys.update(zip(normalize(selected[f"{side}_bank"]), selected[f"{side}_account"]))
    keep = [key in selected_keys for key in zip(normalize(metadata.bank_id), metadata.account_number)]
    # Keep only metadata rows for those accounts and restore the Kaggle headers
    # so the Glue job reads the sample exactly like the real file.
    metadata.loc[keep].rename(columns={
        "bank_name": "Bank Name", "bank_id": "Bank ID", "account_number": "Account Number",
        "entity_id": "Entity ID", "entity_name": "Entity Name",
    }).to_csv(output / "accounts.csv", index=False)
    rates.to_csv(output / "fx.csv", index=False)
    reference.to_parquet(output / "expected.parquet", index=False)
    summary = {
        "rows": len(reference), "amount_usd": float(reference.amount_usd.sum()),
        "amount_usd_asof": float(reference.amount_usd_asof.sum()),
        "positives": int(reference.is_laundering.sum()),
        "scope": "First rows only; validates transformations, not behaviour windows or model quality",
    }
    (output / "expected.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main():
    """Command-line entry point; prints the summary JSON."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--transactions", type=Path, default=Path("dataset/HI-Small_Trans.csv"))
    parser.add_argument("--accounts", type=Path, default=Path("dataset/HI-Small_accounts.csv"))
    parser.add_argument("--fx", type=Path, default=Path("dataset/fx_rates_with_history.csv"))
    parser.add_argument("--output", type=Path, default=Path("output/aws-smoke"))
    parser.add_argument("--rows", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(prepare(args.transactions, args.accounts, args.fx, args.output, args.rows), indent=2))


if __name__ == "__main__":
    main()
