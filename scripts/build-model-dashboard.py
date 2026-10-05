"""Reproduce frozen-model inference and build a local baseline/model comparison."""
import argparse
from pathlib import Path

from bda_pipeline.model_dashboard import export_model_dashboard


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export_model_dashboard(args.scored, args.baseline, args.model_dir, args.output,
                           Path(__file__).resolve().parents[1] / 'dashboard')
    print(f'Built {args.output}; no retraining or promotion')
