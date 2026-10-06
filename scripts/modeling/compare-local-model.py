"""Compare two bounded XGBoost candidates with the full-population baseline."""
import argparse
from pathlib import Path

from bda_pipeline.model import compare_models


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=120)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    compare_models(args.scored, args.snapshot, args.output, args.rounds, args.threads)
