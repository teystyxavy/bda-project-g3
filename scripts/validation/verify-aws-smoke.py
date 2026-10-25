"""Compare downloaded cloud Parquet against the prepared local smoke reference.

Purpose
-------
Second half of the Glue curation smoke test. ``prepare-aws-smoke.py``
created a 1,000-row raw sample and ``expected.parquet`` (the local pandas
answer). After the sample was uploaded to S3 raw and the Glue curation job
ran, the job's curated Parquet output is downloaded and compared here.

The comparison is schema-aware so it works with both versions of the job:
the original deployed smoke job (no ``transaction_id``, no behaviour
features, no ``source_row_index``) and the extended job that adds them. The
expected frame is adjusted to whatever columns the cloud output has, then
both frames are sorted identically and compared value by value
(rtol 1e-10, atol 1e-6 for floating USD amounts).

Output: a JSON summary printed (and optionally written to ``--report``)
with row/column counts, USD totals, positive labels and missing join/FX counts.
No AWS calls; download the output first.

Usage::

    python scripts/validation/verify-aws-smoke.py --curated <downloaded parquet> [--report out.json]
"""

import argparse
import json
from pathlib import Path

import pandas as pd
# Local behaviour-feature reference (trailing 1h/24h/7d counts per sender).
from bda_pipeline.features.behaviour import add_behaviour


def verify(expected_path, curated_path):
    """Compare the cloud curated output with the expected local reference.

    Args:
        expected_path: Path to ``expected.parquet`` from prepare-aws-smoke.py.
        curated_path: Path to the downloaded Glue output (file or folder).

    Returns:
        dict summary with ``status='passed'`` if everything matches.

    Raises:
        ValueError: if the column sets differ.
        AssertionError: (from pandas) if any value differs.
    """
    expected = pd.read_parquet(expected_path)
    actual = pd.read_parquet(curated_path)
    # Support both the original deployed smoke schema and the extended job.
    if "transaction_id" in actual:
        expected["transaction_id"] = "HI-Small:" + expected.source_row_index.astype(str)
    if "txn_count_1h" in actual:
        expected = add_behaviour(expected)
    if "source_row_index" not in actual:
        expected = expected.drop(columns=["source_row_index"])
    if set(actual.columns) != set(expected.columns):
        raise ValueError("Cloud and local output schemas differ")
    columns = sorted(expected.columns)
    # Normalise types that legitimately differ between Spark and pandas output:
    # dates as text, timestamps as pandas datetimes.
    for frame in [actual, expected]:
        frame["txn_date"] = frame.txn_date.astype(str)
        frame["timestamp"] = pd.to_datetime(frame.timestamp)
    # Sorting by all columns gives a deterministic row order without needing a key.
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
    # Default matches prepare-aws-smoke.py's default output folder.
    parser.add_argument("--expected", type=Path, default=Path("output/aws-smoke/expected.parquet"))
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = json.dumps(verify(args.expected, args.curated), indent=2)
    if args.report:
        # 'x' mode: never overwrite an existing report.
        args.report.parent.mkdir(parents=True, exist_ok=True)
        with args.report.open("x", encoding="utf-8") as target:
            target.write(report)
    print(report)


if __name__ == "__main__":
    main()
