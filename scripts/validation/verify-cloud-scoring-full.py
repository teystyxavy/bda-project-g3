"""Verify all cloud scores before exporting a full-population dashboard locally.

Purpose
-------
Command-line wrapper around ``bda_pipeline.scoring.cloud_scores.verify_full_scores``.
After the Glue job ``is459-g3-score-frozen`` scored all 5,078,345 curated
transactions on AWS (BQ1), its output is downloaded and this check proves,
before anything is published to the dashboard, that:

* the job run SUCCEEDED and its configuration (output path, expected rows,
  model manifest SHA-256, success-marker setting) matches what was reviewed;
* the zero-byte, AES256-encrypted ``_SUCCESS`` marker receipt belongs to
  that run and output location;
* the frozen model artifacts verify against their manifest and match the
  reference population;
* every row's features and model score match a native local re-score
  (processed in bounded batches of ``--batch-size`` rows).

Arguments (all paths are local files/folders):
    --curated       downloaded Glue scoring output (Parquet)
    --reference     local reference Parquet the frozen model was built from
    --model-dir     frozen model release (model-manifest.json etc.)
    --run-report    saved ``aws glue get-job-run`` JSON
    --job-report    saved ``aws glue get-job`` JSON
    --marker-report saved receipt of the ``_SUCCESS`` object's metadata
    --output        fresh folder for the verification outputs
    --expected-rows / --batch-size  population size and batch bound

The report is printed as JSON. No AWS calls are made here.
"""
import argparse
import json
from pathlib import Path

from bda_pipeline.scoring.cloud_scores import verify_full_scores


# Script entry point (there is no main(); the logic lives in the shared library).
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    # argparse turns '--model-dir' into args.model_dir, etc.
    for name in ['curated', 'reference', 'model-dir', 'run-report', 'job-report', 'marker-report', 'output']:
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--expected-rows', type=int, default=5_078_345)
    parser.add_argument('--batch-size', type=int, default=100_000)
    args = parser.parse_args()
    report = verify_full_scores(args.curated, args.reference, args.model_dir, args.run_report,
                                args.job_report, args.marker_report, args.output, args.expected_rows, args.batch_size)
    print(json.dumps(report, indent=2))
