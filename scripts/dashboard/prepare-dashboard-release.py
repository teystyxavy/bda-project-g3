"""Prepare four reviewed dashboard assets locally; no AWS calls.

Where this sits in the pipeline
-------------------------------
The final gate before the dashboard is published to the S3 site bucket and
CloudFront. ``bda_pipeline.control.release.prepare_release`` copies exactly the four
runtime files (``index.html``, ``dashboard.css``, ``dashboard.js``,
``dashboard.json``) from a built dashboard folder into a fresh release folder,
and refuses to continue if:

* a file is missing, a symlink, or over 10 MB (or the package is over 12 MB);
* the snapshot is not a complete, full-population build, or lacks the frozen
  model's provenance hash;
* row-level labels or other sensitive content would be shipped (aggregate
  metrics are allowed; individual "is laundering" labels in the queue are not).

It writes a ``manifest.json`` (outside the served files) with hashes and
``status='prepared-not-published'``: publishing is a separate, explicit step.

Usage
-----
    python scripts/dashboard/prepare-dashboard-release.py \
        --source output/model-dashboard --output output/release-YYYYMMDD
"""
import argparse
from pathlib import Path

from bda_pipeline.control.release import prepare_release


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # --source: built dashboard folder; --output: new folder outside --source.
    result = prepare_release(args.source, args.output)
    print(f'Prepared {len(result["files"])} assets, {result["total_bytes"]:,} bytes; not published')
