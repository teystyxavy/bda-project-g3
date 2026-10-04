"""Prepare a bounded raw sample and a local reference without contacting AWS."""

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

from bda_pipeline.transform import build_curated_transactions, read_accounts, read_transactions


def prepare(transactions, accounts, fx, output, rows=1000):
    if rows <= 0:
        raise ValueError("rows must be positive")
    if output.exists():
        raise FileExistsError(f"Use a fresh output directory: {output}")
    selected = read_transactions(transactions, nrows=rows)
    metadata = read_accounts(accounts)
    rates = pd.read_csv(fx)
    reference = build_curated_transactions(selected, metadata, rates)
    for side in ["from", "to"]:
        if reference[f"{side}_entity_id"].isna().any():
            raise ValueError("Sample has unmatched account metadata")
    prior = selected.copy()
    prior["timestamp"] = pd.to_datetime(prior.timestamp) - pd.Timedelta(days=1)
    reference["amount_usd_asof"] = build_curated_transactions(prior, metadata, rates).amount_usd
    output.mkdir(parents=True)
    with transactions.open(newline="", encoding="utf-8-sig") as source, (output / "transactions.csv").open("w", newline="", encoding="utf-8") as target:
        reader, writer = csv.reader(source), csv.writer(target)
        writer.writerow(next(reader))
        for index, row in enumerate(reader):
            if index >= rows:
                break
            writer.writerow(row)
    normalize = lambda values: values.astype("string").str.lstrip("0").replace("", "0")
    selected_keys = set()
    for side in ["from", "to"]:
        selected_keys.update(zip(normalize(selected[f"{side}_bank"]), selected[f"{side}_account"]))
    keep = [key in selected_keys for key in zip(normalize(metadata.bank_id), metadata.account_number)]
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
