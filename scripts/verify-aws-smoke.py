"""Compare downloaded cloud Parquet against the prepared local smoke reference."""

import argparse
import json
from pathlib import Path

import pandas as pd


def verify(expected_path, curated_path):
    expected = pd.read_parquet(expected_path).drop(columns=["source_row_index"])
    actual = pd.read_parquet(curated_path)
    if set(actual.columns) != set(expected.columns):
        raise ValueError("Cloud and local output schemas differ")
    columns = sorted(expected.columns)
    for frame in [actual, expected]:
        frame["txn_date"] = frame.txn_date.astype(str)
        frame["timestamp"] = pd.to_datetime(frame.timestamp)
    actual = actual[columns].sort_values(columns).reset_index(drop=True)
    expected = expected[columns].sort_values(columns).reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False, atol=1e-6, rtol=1e-10)
    return {
        "status": "passed", "rows": len(actual), "columns": len(columns),
        "amount_usd": float(actual.amount_usd.sum()),
        "amount_usd_asof": float(actual.amount_usd_asof.sum()),
        "positives": int(actual.is_laundering.sum()),
        "missing_accounts": int((actual.from_entity_id.isna() | actual.to_entity_id.isna()).sum()),
        "missing_fx": int((actual.amount_usd.isna() | actual.amount_usd_asof.isna()).sum()),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", type=Path, default=Path("output/aws-smoke/expected.parquet"))
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = json.dumps(verify(args.expected, args.curated), indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as target:
            target.write(report)
    print(report)


if __name__ == "__main__":
    main()
