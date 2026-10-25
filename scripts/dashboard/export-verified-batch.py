"""Create full-population baseline scores and dashboards from verified cloud data.

Where this sits in the pipeline
-------------------------------
After the Glue curate job writes the curated, date-partitioned Parquet and the
verification step re-checks it against the raw CSVs (writing ``proof.json``),
the *whole* population (about 5.08 million HI-Small transactions) is scored
with the rule-based BQ1 baseline and aggregated into BQ2 corridors. In AWS this
runs inside the SageMaker "baseline" processing job
(``ingest_compute.baseline_stage``). This script runs the same function,
``bda_pipeline.control.export.export_verified_batch``, locally on a downloaded copy of
the verified cloud data, which also produces extra CSV reports.

Inputs
------
* ``--curated``: hive-partitioned curated Parquet folder (``txn_date=.../``).
* ``--verification``: ``proof.json``; scoring refuses to run unless it says
  ``status: passed``, and totals must reconcile with it.

Output: a new ``--output`` folder with full-population BQ1 scores, BQ2
corridor outputs, ``run_report.json`` (row counts, P99 thresholds, metrics
per period) and the dashboard files.

Usage
-----
    python scripts/dashboard/export-verified-batch.py \
        --curated downloads/curated --verification downloads/proof.json --output output/full-batch
"""
import argparse
from pathlib import Path
from bda_pipeline.control.export import export_verified_batch


def main():
    """Score and export the verified batch, then print row count and test metrics.

    Raises:
        ValueError: (from ``export_verified_batch``) on a failed/missing proof,
            an existing output folder, or any integrity/reconciliation failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--curated', type=Path, required=True)
    parser.add_argument('--verification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # Last argument: <repo>/dashboard, the static site shell copied to output.
    report = export_verified_batch(args.curated, args.verification, args.output,
                                   Path(__file__).resolve().parents[2] / 'dashboard')
    print(f"Exported {report['rows']:,} full-population scores and dashboard to {args.output}")
    # 'test' = the held-out Sep 9-10 period (rules are fitted on Sep 1-6 only).
    print(f"Test metrics: {report['metrics']['test']}")


if __name__ == '__main__':
    main()
