"""Prepare frozen-model artifacts and a native parity cohort without AWS calls.

Where this sits in the pipeline
-------------------------------
BQ1 risk scores are produced in AWS by a Glue job that loads a *frozen*
(already trained, never retrained in this step) XGBoost model. Before that
model can be deployed, this script packages it locally with
``bda_pipeline.scoring.inference.prepare_inference``:

* copies the selected model file and its JSON companions
  (``comparison.json``, ``features.json``, ``selection.json``) into
  ``<output>/artifacts/`` and writes a ``model-manifest.json`` of their hashes;
* zips the pure-Python ``src/bda_pipeline`` package into ``bda_pipeline.zip``
  (the code the Glue job imports);
* builds a "parity cohort": up to 256 transactions from each of four dates,
  scored locally, saved as ``fixture/input.parquet``, ``expected.parquet`` and
  ``expected-queue.parquet``. After deployment the cloud job's scores for the
  same rows can be compared with these to prove the cloud and local scoring
  agree ("native parity");
* writes ``package.json`` with hashes and ``status='local-package-not-deployed'``.

It checks that ``--scored`` is the same dataset the model was selected on
(hash recorded in ``selection.json``). No AWS calls are made.

Usage
-----
    python scripts/aws/prepare-cloud-scoring.py \
        --model-dir output/models --scored output/local/scored.parquet \
        --output output/cloud-scoring-package
"""
import argparse
import json
from pathlib import Path

from bda_pipeline.scoring.inference import prepare_inference


if __name__ == '__main__':
    # --model-dir: trained model folder; --scored: locally scored Parquet used as
    # the parity source; --output: fresh folder for the package.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # The last argument is the source package folder that gets zipped.
    metadata = prepare_inference(args.model_dir, args.scored, args.output,
                                 Path(__file__).resolve().parents[2] / 'src/bda_pipeline')
    print(json.dumps(metadata, indent=2))
