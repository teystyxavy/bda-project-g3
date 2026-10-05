"""Verify downloaded successful Glue smoke output against frozen native fixtures."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from bda_pipeline.inference import worker_ranker
from bda_pipeline.model import digest


def verify(curated, package, run_report, forensic=False):
    run = json.loads(Path(run_report).read_text(encoding="utf-8-sig"))["JobRun"]
    allowed_states = {"SUCCEEDED", "FAILED"} if forensic else {"SUCCEEDED"}
    if run["JobRunState"] not in allowed_states or run["JobName"] != "is459-g3-score-frozen":
        raise ValueError("Require a successful scoring job report")
    package = Path(package)
    metadata = json.loads((package / "package.json").read_text())
    expected = pd.read_parquet(package / "fixture/expected.parquet")
    actual = ds.dataset(curated, format="parquet", partitioning="hive").to_table().to_pandas()
    if set(actual.columns) != set(expected.columns) | {"txn_date"}:
        raise ValueError("Unexpected output columns or evaluation labels")
    if len(actual) != 1024 or len(expected) != 1024 or metadata["cohort_rows"] != 1024:
        raise ValueError("Expected exactly 1,024 cohort rows")
    if actual.transaction_id.nunique() != 1024 or actual.source_row_index.nunique() != 1024:
        raise ValueError("Duplicate or missing output identities")
    actual = actual.sort_values("source_row_index").reset_index(drop=True)
    expected = expected.sort_values("source_row_index").reset_index(drop=True)
    features = expected.columns.drop("model_score")
    pd.testing.assert_frame_equal(actual[features], expected[features], check_dtype=False)
    np.testing.assert_allclose(actual.model_score, expected.model_score, rtol=1e-6, atol=1e-7)
    if not np.isfinite(actual.model_score).all() or not actual.model_score.between(0, 1).all():
        raise ValueError("Invalid ranking scores")
    if not actual.txn_date.astype(str).eq(actual.timestamp.dt.strftime("%Y-%m-%d")).all():
        raise ValueError("Date partition differs from source timestamp")
    ranker = worker_ranker(str(package / "artifacts"), metadata["manifest_sha256"], 1)
    queue = ranker.queue(actual, actual.model_score.to_numpy())
    expected_queue = pd.read_parquet(package / "fixture/expected-queue.parquet")
    if queue.transaction_id.tolist() != expected_queue.transaction_id.tolist():
        raise ValueError("Cohort top-100 IDs/order differ from native reference")
    if queue.model_evidence.tolist() != expected_queue.model_evidence.tolist():
        raise ValueError("Locally reconstructed explanation text differs")
    np.testing.assert_allclose(queue.explanation_total_margin, expected_queue.explanation_total_margin,
                               rtol=1e-6, atol=1e-6)
    return {
        "status": "passed" if run["JobRunState"] == "SUCCEEDED" else "forensic-parity-only-failed-run",
        "job_run_id": run["Id"], "job_state": run["JobRunState"],
        "pipeline_passed": run["JobRunState"] == "SUCCEEDED", "rows": len(actual),
        "unique_ids": int(actual.transaction_id.nunique()),
        "feature_columns_checked": list(features), "labels_excluded": True,
        "score_rtol": 1e-6, "score_atol": 1e-7,
        "max_absolute_score_difference": float(np.abs(actual.model_score - expected.model_score).max()),
        "date_partitions": sorted(actual.txn_date.astype(str).unique().tolist()),
        "cohort_top100_ids_match": True,
        "explanations": "reconstructed locally from cloud features and frozen model; not emitted by cloud job",
        "manifest_sha256": metadata["manifest_sha256"],
        "native_fixture_sha256": digest(package / "fixture/expected.parquet"),
        "output_parquet_sha256": {str(p.relative_to(curated)): digest(p)
                                  for p in sorted(Path(curated).rglob("*.parquet"))},
        "scope": "1024-row cohort parity only; no full-data evaluation or model retuning",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--curated", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--run-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--forensic", action="store_true",
                        help="Inspect failed-run files without marking the pipeline/output valid")
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a fresh report path")
    report = verify(args.curated, args.package, args.run_report, args.forensic)
    with args.report.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2, allow_nan=False)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
