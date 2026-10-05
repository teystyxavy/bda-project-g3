"""Reconcile downloaded full-run Parquet with local corridors and sender histories."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from bda_pipeline.analytics import corridor_totals


def verify(curated, reference, corridor_reference, expected_rows):
    expected = pd.read_parquet(reference)
    ids = set(expected.source_row_index)
    seen = np.zeros(expected_rows, dtype=bool)
    selected, aggregates = [], []
    rows = positives = 0
    for batch in ds.dataset(curated, format="parquet", partitioning="hive").to_batches(batch_size=100_000):
        frame = batch.to_pandas()
        indices = frame.source_row_index.to_numpy(dtype=np.int64)
        if ((indices < 0) | (indices >= expected_rows)).any():
            raise ValueError("Source row outside expected range")
        if seen[indices].any() or len(np.unique(indices)) != len(indices):
            raise ValueError("Duplicate source row index")
        seen[indices] = True
        if not frame.transaction_id.eq("HI-Small:" + frame.source_row_index.astype(str)).all():
            raise ValueError("Transaction ID does not match source index")
        if frame[["from_entity_id", "to_entity_id", "amount_usd", "amount_usd_asof"]].isna().any().any():
            raise ValueError("Missing metadata or FX")
        rows += len(frame)
        positives += int(frame.is_laundering.sum())
        selected.append(frame.loc[frame.source_row_index.isin(ids)].copy())
        aggregates.append(corridor_totals(frame))
    if rows != expected_rows or not seen.all():
        raise ValueError("Full row coverage failed")
    actual = pd.concat(selected, ignore_index=True)
    features = ["txn_count_1h", "txn_count_24h", "txn_count_7d",
                "distinct_counterparties_24h", "distinct_counterparties_7d"]
    columns = ["source_row_index", "transaction_id", "timestamp", "from_bank", "from_account",
               "to_bank", "to_account", "amount_usd", "amount_usd_asof", "is_laundering", *features]
    actual = actual[columns].sort_values("source_row_index").reset_index(drop=True)
    expected = expected[columns].sort_values("source_row_index").reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False, rtol=1e-10, atol=1e-6)
    keys = ["txn_date", "from_bank", "to_bank", "payment_currency", "receiving_currency"]
    daily = pd.concat(aggregates, ignore_index=True).groupby(keys, observed=True)[["transaction_count", "volume_usd"]].sum().reset_index()
    expected_daily = pd.read_parquet(corridor_reference)
    for frame in [daily, expected_daily]:
        frame["txn_date"] = frame.txn_date.astype(str)
    daily = daily.sort_values(keys).reset_index(drop=True)
    expected_daily = expected_daily[daily.columns].sort_values(keys).reset_index(drop=True)
    pd.testing.assert_frame_equal(daily, expected_daily, check_dtype=False, rtol=1e-10, atol=1e-6)
    return dict(status="passed", rows=rows, positives=positives, unique_source_ids=int(seen.sum()),
                behaviour_reference_rows=len(actual), behaviour_features=features,
                daily_corridors=len(daily), amount_usd=float(daily.volume_usd.sum()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=Path("output/evaluation-20261004/bq1_scored.parquet"))
    parser.add_argument("--corridors", type=Path, default=Path("output/analysis-20261004/bq2_daily_corridors.parquet"))
    parser.add_argument("--expected-rows", type=int, default=5_078_345)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a new report path")
    report = verify(args.curated, args.reference, args.corridors, args.expected_rows)
    with args.report.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, allow_nan=False)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
