"""Reproduce frozen-model inference and build a local baseline/model comparison.

Where this sits in the pipeline
-------------------------------
BQ1 has two ranked review queues: the transparent rule-based *baseline* and
the XGBoost *model*. This script takes an existing full-population baseline
dashboard (``dashboard.json`` from ``export-verified-batch.py``) and adds the
model's queue and metrics by re-running inference with the *frozen* model
(``bda_pipeline.model_dashboard.export_model_dashboard``). It never retrains
or promotes a model.

Safety checks done by the library: the scored Parquet's hash must equal the
hash recorded when the model was selected *and* the one in the baseline
snapshot (all three describe the same data), population sizes must agree,
and reloaded metrics must equal the stored values.

Inputs
------
* ``--scored``: full-population ``bq1_scored.parquet``.
* ``--baseline``: baseline ``dashboard.json`` (must be full scope).
* ``--model-dir``: frozen model folder (artifact hashes are verified).
* ``--cloud-verification`` (optional): a passed cloud verification report;
  then the dashboard uses the cloud-computed scores (from the checked score
  cache next to it) instead of local inference.

Output: a new ``--output`` folder with the updated ``dashboard.json`` and the
static site assets from ``<repo>/dashboard``.

Usage
-----
    python scripts/dashboard/build-model-dashboard.py --scored ... \
        --baseline .../dashboard.json --model-dir output/models --output output/model-dashboard
"""
import argparse
from pathlib import Path

from bda_pipeline.model_dashboard import export_model_dashboard


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--model-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cloud-verification', type=Path,
                        help='Passed full-population report and sibling checked cloud score cache')
    args = parser.parse_args()
    # The fifth argument is the folder of static assets copied into the output.
    export_model_dashboard(args.scored, args.baseline, args.model_dir, args.output,
                           Path(__file__).resolve().parents[2] / 'dashboard', args.cloud_verification)
    print(f'Built {args.output}; no retraining or promotion')
