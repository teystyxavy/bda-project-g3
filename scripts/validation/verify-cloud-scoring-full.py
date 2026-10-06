"""Verify all cloud scores before exporting a full-population dashboard locally."""
import argparse
import json
from pathlib import Path

from bda_pipeline.cloud_scores import verify_full_scores


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['curated', 'reference', 'model-dir', 'run-report', 'job-report', 'marker-report', 'output']:
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--expected-rows', type=int, default=5_078_345)
    parser.add_argument('--batch-size', type=int, default=100_000)
    args = parser.parse_args()
    report = verify_full_scores(args.curated, args.reference, args.model_dir, args.run_report,
                                args.job_report, args.marker_report, args.output, args.expected_rows, args.batch_size)
    print(json.dumps(report, indent=2))
