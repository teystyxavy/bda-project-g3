"""Build static HTML and JSON ready for a local server or future S3 hosting.

Where this sits in the pipeline
-------------------------------
The last, presentation step: the compliance/treasury dashboard is a static
site (``dashboard/index.html`` + ``dashboard.css`` + ``dashboard.js``) that
reads one data file, ``dashboard.json``. In AWS the SageMaker export stage
builds it and the site is served from S3 behind CloudFront; this script builds
the same thing *locally* from Parquet outputs so it can be previewed with any
static web server (e.g. ``python -m http.server``).

Inputs
------
* ``--scored``: BQ1 scored transactions Parquet (e.g. ``bq1_scored.parquet``
  from ``scripts/local/run-local-analysis.py``) - the ranked AML review queue.
* ``--corridors``: BQ2 daily corridor totals Parquet
  (``bq2_daily_corridors.parquet``) - USD volume per bank/currency corridor.

Output (new folder, default ``output/dashboard``)
-------------------------------------------------
``dashboard.json`` (snapshot with SHA-256 of both inputs for provenance),
``validation-diagnostics.json`` and copies of the three static assets.

Usage
-----
    python scripts/dashboard/build-dashboard.py \
        --scored output/local/bq1_scored.parquet \
        --corridors output/local/bq2_daily_corridors.parquet --output output/dashboard-v1
"""
import argparse
from pathlib import Path
import shutil
from bda_pipeline.dashboards.dashboard import export_dashboard


def main():
    """Build the snapshot JSON and copy the static site files into ``--output``.

    Exits with a usage error if ``--output`` already exists, so an earlier
    build is never overwritten or mixed with a new one.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--corridors', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('output/dashboard'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output directory; existing builds are preserved')
    # Computes the dashboard snapshot (queue, metrics, corridor/HHI data) and
    # writes dashboard.json + validation-diagnostics.json.
    snapshot = export_dashboard(args.scored, args.corridors, args.output)
    # The HTML/CSS/JS shell lives in <repo>/dashboard and is data-agnostic.
    for name in ['index.html', 'dashboard.css', 'dashboard.js']:
        shutil.copyfile(Path(__file__).resolve().parents[2] / 'dashboard' / name, args.output / name)
    print(f"Built {args.output}: {snapshot['full_rows']:,} treasury rows; {snapshot['cohort_rows']:,} cohort rows")


if __name__ == '__main__':
    main()
