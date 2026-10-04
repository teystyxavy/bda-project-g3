"""Re-evaluate saved cohort features without rescanning the full transaction file."""
import argparse
import json
from pathlib import Path
import pandas as pd
from bda_pipeline.analytics import evaluate_baseline


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new output directory')
    ranked, thresholds, metrics = evaluate_baseline(pd.read_parquet(args.input))
    args.output.mkdir(parents=True)
    ranked.to_parquet(args.output / 'bq1_scored.parquet', index=False)
    test = ranked.loc[ranked.timestamp.ge('2022-09-09') & ranked.timestamp.lt('2022-09-11')]
    test.head(100).drop(columns=['is_laundering']).to_csv(args.output / 'review_queue_test.csv', index=False)
    report = dict(input=str(args.input), training_p99=thresholds, metrics=metrics,
                  scope='Sender cohort; exploratory rules informed by prior full-dataset EDA',
                  splits='Train Sep 1-6; validation Sep 7-8; test Sep 9-10; sparse tail Sep 11-18')
    (args.output / 'evaluation.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
