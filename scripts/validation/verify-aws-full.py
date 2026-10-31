"""Reconcile downloaded full-run Parquet with local corridors and sender histories.

Purpose
-------
Acceptance check for the *full* Glue curation run (all 5,078,345 HI-Small
transactions). The curated Parquet written by Glue to S3 is downloaded
locally (Hive-style ``txn_date=YYYY-MM-DD`` partition folders) and checked
against independent local references:

1. **Coverage**: every ``source_row_index`` 0..N-1 appears exactly once (no
   lost or duplicated rows), ``transaction_id`` = ``'HI-Small:<index>'``,
   and no row lacks entity metadata or FX-converted amounts.
2. **Row-level parity**: for every row also present in the local BQ1
   reference (``--reference``), identifiers, USD amounts, label and the five
   behaviour features must equal the local values (rtol 1e-10, atol 1e-6).
3. **Corridor parity (BQ2)**: daily totals per (date, from bank, to bank,
   payment currency, receiving currency) must equal the local corridor
   reference (``--corridors``).

Memory: the data is streamed in 100,000-row batches; only a boolean
"seen" array, the reference subset and small per-batch aggregates are kept.

Output: a JSON report at ``--report`` (new path). No AWS calls.

Usage::

    python scripts/validation/verify-aws-full.py --curated <downloaded dir> --report out.json
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

# corridor_totals: groups rows by day/bank pair/currency pair -> count + USD sum.
from bda_pipeline.analytics import corridor_totals
from bda_pipeline.batch import FEATURES


def verify(curated, reference, corridor_reference, expected_rows):
    """Run coverage, row-level and corridor reconciliation.

    Args:
        curated: local folder of downloaded Glue output Parquet (hive partitions).
        reference: local BQ1 scored Parquet whose rows are compared exactly.
        corridor_reference: local BQ2 daily corridor Parquet.
        expected_rows: total rows the full dataset must contain.

    Returns:
        dict report (status, row/positive counts, corridor count, USD total).

    Raises:
        ValueError / AssertionError: on any coverage or value mismatch.
    """
    expected = pd.read_parquet(reference)
    ids = set(expected.source_row_index)
    # One flag per expected source row; used to detect duplicates and gaps.
    seen = np.zeros(expected_rows, dtype=bool)
    selected, aggregates = [], []
    rows = positives = 0
    # partitioning="hive" turns the txn_date=... folder names back into a column.
    for batch in ds.dataset(curated, format="parquet", partitioning="hive").to_batches(batch_size=100_000):
        frame = batch.to_pandas()
        indices = frame.source_row_index.to_numpy(dtype=np.int64)
        # Indices must be inside [0, expected_rows) before using them to index 'seen'.
        if ((indices < 0) | (indices >= expected_rows)).any():
            raise ValueError("Source row outside expected range")
        # Duplicate either across batches (already seen) or within this batch.
        if seen[indices].any() or len(np.unique(indices)) != len(indices):
            raise ValueError("Duplicate source row index")
        seen[indices] = True
        # IDs are derived from the source row index, so they must agree.
        if not frame.transaction_id.eq("HI-Small:" + frame.source_row_index.astype(str)).all():
            raise ValueError("Transaction ID does not match source index")
        # Missing entity IDs = failed account join; missing USD = missing FX rate.
        if frame[["from_entity_id", "to_entity_id", "amount_usd", "amount_usd_asof"]].isna().any().any():
            raise ValueError("Missing metadata or FX")
        rows += len(frame)
        positives += int(frame.is_laundering.sum())
        # Keep only rows that the local reference also has (for exact comparison).
        selected.append(frame.loc[frame.source_row_index.isin(ids)].copy())
        # Partial corridor sums per batch; combined after the loop.
        aggregates.append(corridor_totals(frame))
    # Exactly N rows and all flags set => every source row appears once.
    if rows != expected_rows or not seen.all():
        raise ValueError("Full row coverage failed")
    actual = pd.concat(selected, ignore_index=True)
    features = list(FEATURES)
    columns = ["source_row_index", "transaction_id", "timestamp", "from_bank", "from_account",
               "to_bank", "to_account", "amount_usd", "amount_usd_asof", "is_laundering", *features]
    actual = actual[columns].sort_values("source_row_index").reset_index(drop=True)
    expected = expected[columns].sort_values("source_row_index").reset_index(drop=True)
    # check_dtype=False: Parquet from Spark and pandas may use different int/str
    # dtypes for the same values; the values themselves must match.
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False, rtol=1e-10, atol=1e-6)
    # Combine per-batch partial sums into one row per corridor per day.
    keys = ["txn_date", "from_bank", "to_bank", "payment_currency", "receiving_currency"]
    daily = pd.concat(aggregates, ignore_index=True).groupby(keys, observed=True)[["transaction_count", "volume_usd"]].sum().reset_index()
    expected_daily = pd.read_parquet(corridor_reference)
    # Dates may be date objects on one side and strings on the other; compare as text.
    for frame in [daily, expected_daily]:
        frame["txn_date"] = frame.txn_date.astype(str)
    daily = daily.sort_values(keys).reset_index(drop=True)
    expected_daily = expected_daily[daily.columns].sort_values(keys).reset_index(drop=True)
    pd.testing.assert_frame_equal(daily, expected_daily, check_dtype=False, rtol=1e-10, atol=1e-6)
    return dict(status="passed", rows=rows, positives=positives, unique_source_ids=int(seen.sum()),
                behaviour_reference_rows=len(actual), behaviour_features=features,
                daily_corridors=len(daily), amount_usd=float(daily.volume_usd.sum()))


def main():
    """Command-line entry point; writes the report exclusively and prints it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=Path("output/evaluation-20261004/bq1_scored.parquet"))
    parser.add_argument("--corridors", type=Path, default=Path("output/analysis-20261004/bq2_daily_corridors.parquet"))
    parser.add_argument("--expected-rows", type=int, default=5_078_345)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    # Never overwrite an earlier report.
    if args.report.exists():
        parser.error("Use a new report path")
    report = verify(args.curated, args.reference, args.corridors, args.expected_rows)
    with args.report.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, allow_nan=False)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
