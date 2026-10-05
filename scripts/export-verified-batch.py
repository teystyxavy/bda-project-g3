"""Create full-population baseline scores and dashboards from verified cloud data."""
import argparse
from pathlib import Path
from bda_pipeline.batch import export_verified_batch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--curated', type=Path, required=True)
    parser.add_argument('--verification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = export_verified_batch(args.curated, args.verification, args.output,
                                   Path(__file__).resolve().parents[1] / 'dashboard')
    print(f"Exported {report['rows']:,} full-population scores and dashboard to {args.output}")
    print(f"Test metrics: {report['metrics']['test']}")


if __name__ == '__main__':
    main()
