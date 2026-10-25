"""Re-evaluate saved cohort features without rescanning the full transaction file.

Where this sits in the pipeline
-------------------------------
A fast local iteration tool for BQ1. ``run-local-analysis.py`` spends most of
its time reading all ~5 million transactions and computing sender behaviour
features. Once a cohort with those features is saved as Parquet, this script
re-runs only the rule-based baseline evaluation
(``bda_pipeline.baseline.rules.evaluate_baseline``) on it, so rule changes can be
tested in seconds.

Evaluation design (chronological, like real use)
------------------------------------------------
* Train: Sep 1-6 2022 - only used to fit the amount thresholds (99th
  percentile of prior-day USD amount per payment format).
* Validation: Sep 7-8; Test: Sep 9-10; sparse tail: Sep 11-18 (few rows).

Outputs (new ``--output`` folder)
---------------------------------
* ``bq1_scored.parquet`` - every cohort row with risk score and reasons,
  sorted as a review queue.
* ``review_queue_test.csv`` - top 100 rows of the test period, with the
  ``is_laundering`` label removed (what an analyst would actually see).
* ``evaluation.json`` - thresholds, per-period ranking metrics, scope notes.

Usage
-----
    python scripts/local/evaluate-local-risk.py \
        --input output/local/bq1_scored.parquet --output output/local-eval-2
"""
import argparse
import json
from pathlib import Path
import pandas as pd
from bda_pipeline.baseline.rules import evaluate_baseline


def main():
    """Score the saved cohort, write the queue/metrics files and print the report.

    Exits with a usage error if ``--output`` already exists.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output directory')
    # ranked: scored + sorted cohort; thresholds: {payment_format: P99 USD};
    # metrics: ranking metrics for validation / test / sparse_tail.
    ranked, thresholds, metrics = evaluate_baseline(pd.read_parquet(args.input))
    args.output.mkdir(parents=True)
    ranked.to_parquet(args.output / 'bq1_scored.parquet', index=False)
    # Test window [Sep 9, Sep 11). ranked is already in queue order, so head(100)
    # is the top of the analyst's queue; the label is dropped from the CSV.
    test = ranked.loc[ranked.timestamp.ge('2022-09-09') & ranked.timestamp.lt('2022-09-11')]
    test.head(100).drop(columns=['is_laundering']).to_csv(args.output / 'review_queue_test.csv', index=False)
    report = dict(input=str(args.input), training_p99=thresholds, metrics=metrics,
                  scope='Sender cohort; exploratory rules informed by prior full-dataset EDA',
                  splits='Train Sep 1-6; validation Sep 7-8; test Sep 9-10; sparse tail Sep 11-18')
    # allow_nan=False: fail instead of writing invalid JSON if a metric is NaN.
    (args.output / 'evaluation.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
