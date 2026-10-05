"""Prepare frozen-model artifacts and a native parity cohort without AWS calls."""
import argparse
import json
from pathlib import Path

from bda_pipeline.inference import prepare_inference


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    metadata = prepare_inference(args.model_dir, args.scored, args.output,
                                 Path(__file__).resolve().parents[1] / 'src/bda_pipeline')
    print(json.dumps(metadata, indent=2))
