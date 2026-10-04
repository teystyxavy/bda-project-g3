"""Build static HTML and JSON ready for a local server or future S3 hosting."""
import argparse
from pathlib import Path
import shutil
from bda_pipeline.dashboard import export_dashboard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--corridors', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('output/dashboard'))
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output directory; existing builds are preserved')
    snapshot = export_dashboard(args.scored, args.corridors, args.output)
    for name in ['index.html', 'dashboard.css', 'dashboard.js']:
        shutil.copyfile(Path(__file__).resolve().parents[1] / 'dashboard' / name, args.output / name)
    print(f"Built {args.output}: {snapshot['full_rows']:,} treasury rows; {snapshot['cohort_rows']:,} cohort rows")


if __name__ == '__main__':
    main()
