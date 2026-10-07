"""Compare two bounded XGBoost candidates with the full-population baseline.

Where this sits in the pipeline
-------------------------------
BQ1 asks for a ranked, explainable AML review queue. The rule-based baseline
is the transparent reference; this script checks whether a machine-learning
model ranks suspicious transactions better. It runs
``bda_pipeline.model.compare_models`` locally (the same logic SageMaker
training uses), which:

1. verifies ``--scored`` (full-population ``bq1_scored.parquet``) matches the
   hash in the verified dashboard ``--snapshot`` and covers every row;
2. trains each XGBoost candidate on Sep 1-6 with early stopping on the
   validation period (Sep 7-8, AUC-PR);
3. picks a winner with a pre-registered rule and checks it against the
   baseline on validation *before* looking at the test data (writes
   ``selection.json`` and ``features.json``), so the test result is honest;
4. scores validation, test (Sep 9-10) and the sparse tail, compares with the
   baseline, writes explained top-100 queues and ``comparison.json``.

The selected model files in ``--output`` are what ``prepare-cloud-scoring.py``
and ``prepare-ingestion.py`` later package as the frozen model.

Usage
-----
    python scripts/modeling/compare-local-model.py \
        --scored output/full-batch/bq1_scored.parquet \
        --snapshot output/full-batch/dashboard.json --output output/models --rounds 120 --threads 4
"""
import argparse
from pathlib import Path

from bda_pipeline.model import compare_models


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    # --rounds: maximum boosting rounds per candidate (early stopping may end
    # sooner); --threads: CPU threads for XGBoost.
    parser.add_argument('--rounds', type=int, default=120)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    compare_models(args.scored, args.snapshot, args.output, args.rounds, args.threads)
