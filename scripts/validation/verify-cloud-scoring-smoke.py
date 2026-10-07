"""Verify downloaded successful Glue smoke output against frozen native fixtures.

Purpose
-------
Smoke-test acceptance for the BQ1 Glue scoring job
(``is459-g3-score-frozen``, ``jobs/score_transactions.py``). Before scoring
all 5 million rows, the job was run on a fixed 1,024-row cohort. This script
compares the downloaded Glue output with the *native* fixture produced
locally by the frozen XGBoost model (inside the cloud-scoring package):

* the Glue run report must be SUCCEEDED (or FAILED in ``--forensic`` mode,
  which inspects files without marking the pipeline as passed);
* output columns = fixture columns + the ``txn_date`` partition column, so
  evaluation labels never leak into the scored output;
* exactly 1,024 unique rows; all feature values identical; scores finite, in
  [0, 1] and within rtol 1e-6 / atol 1e-7 of the native scores;
* each row's ``txn_date`` partition equals the date of its timestamp;
* the top-100 review queue rebuilt from the cloud scores has the same IDs in
  the same order, the same explanation text and (within 1e-6) the same
  explanation margins as the native queue.

Explanations (TreeSHAP evidence) are reconstructed locally from cloud
features; the Glue job itself only emits scores.

Output: JSON report at ``--report`` (new path) including SHA-256 hashes of
every output Parquet file. No AWS calls.

Usage::

    python scripts/validation/verify-cloud-scoring-smoke.py --curated <downloaded dir> \\
        --package output/cloud-scoring-verified-20261005 --run-report run.json --report out.json
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

# worker_ranker: loads the frozen model after verifying its manifest hash.
from bda_pipeline.inference import worker_ranker
# digest: SHA-256 of a file.
from bda_pipeline.model import digest


def verify(curated, package, run_report, forensic=False):
    """Compare Glue smoke output with the native fixture and return a report dict.

    Args:
        curated: downloaded Glue output folder (hive ``txn_date=`` partitions).
        package: frozen cloud-scoring package folder (package.json, fixture/,
            artifacts/).
        run_report: saved ``aws glue get-job-run`` JSON for the run.
        forensic: if True, also accept a FAILED run for diagnosis; the report
            status then says the pipeline did not pass.

    Raises:
        ValueError: wrong job/state, unexpected columns, row counts, IDs,
            scores, partitions or queue differences.
        AssertionError: (pandas/numpy testing) feature or score mismatch.
    """
    # utf-8-sig tolerates the BOM that PowerShell adds when saving CLI output.
    run = json.loads(Path(run_report).read_text(encoding="utf-8-sig"))["JobRun"]
    allowed_states = {"SUCCEEDED", "FAILED"} if forensic else {"SUCCEEDED"}
    if run["JobRunState"] not in allowed_states or run["JobName"] != "is459-g3-score-frozen":
        raise ValueError("Require a successful scoring job report")
    package = Path(package)
    metadata = json.loads((package / "package.json").read_text())
    expected = pd.read_parquet(package / "fixture/expected.parquet")
    # Read every partition into one DataFrame (only 1,024 rows).
    actual = ds.dataset(curated, format="parquet", partitioning="hive").to_table().to_pandas()
    # Glue adds txn_date as the partition column; anything else extra (e.g. the
    # is_laundering label) would be a leak, anything missing a bug.
    if set(actual.columns) != set(expected.columns) | {"txn_date"}:
        raise ValueError("Unexpected output columns or evaluation labels")
    if len(actual) != 1024 or len(expected) != 1024 or metadata["cohort_rows"] != 1024:
        raise ValueError("Expected exactly 1,024 cohort rows")
    if actual.transaction_id.nunique() != 1024 or actual.source_row_index.nunique() != 1024:
        raise ValueError("Duplicate or missing output identities")
    # Align rows by the stable source index before comparing.
    actual = actual.sort_values("source_row_index").reset_index(drop=True)
    expected = expected.sort_values("source_row_index").reset_index(drop=True)
    # Every column except the score must be exactly equal.
    features = expected.columns.drop("model_score")
    pd.testing.assert_frame_equal(actual[features], expected[features], check_dtype=False)
    # Scores are ranking scores, so they must be finite and between 0 and 1.
    np.testing.assert_allclose(actual.model_score, expected.model_score, rtol=1e-6, atol=1e-7)
    if not np.isfinite(actual.model_score).all() or not actual.model_score.between(0, 1).all():
        raise ValueError("Invalid ranking scores")
    # The partition must be the transaction's own (UTC) date.
    if not actual.txn_date.astype(str).eq(actual.timestamp.dt.strftime("%Y-%m-%d")).all():
        raise ValueError("Date partition differs from source timestamp")
    # Rebuild the explained top-100 review queue from the *cloud* scores and
    # compare it with the queue built from native scores.
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
    """Command-line entry point; writes the report exclusively and prints it."""
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
