"""Prepare four reviewed dashboard assets locally; no AWS calls."""
import argparse
from pathlib import Path

from bda_pipeline.release import prepare_release


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = prepare_release(args.source, args.output)
    print(f'Prepared {len(result["files"])} assets, {result["total_bytes"]:,} bytes; not published')
