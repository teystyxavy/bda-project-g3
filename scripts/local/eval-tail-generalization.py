"""Scores a dense-period-trained model against tail-only rows it never saw during training.

Not a held-out test in the usual sense (the tail wasn't part of any split, just excluded
entirely) - this answers "does a model trained only on normal-density data still catch
the tail's heavily-labeled-positive pattern?", reported separately from the clean
dense-only validation/test numbers.

Usage:
    python scripts/local/eval-tail-generalization.py \\
        --models DIR --scored bq1_scored.parquet --tail-start 2022-11-06
"""
import argparse
from pathlib import Path

import pyarrow.dataset as ds
import pyarrow.compute as pc

from bda_pipeline.analytics import ranking_metrics
from bda_pipeline.model import SavedRanker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--scored', type=Path, required=True)
    parser.add_argument('--tail-start', default='2022-11-06')
    parser.add_argument('--threads', type=int, default=8)
    parser.add_argument('--k', type=int, default=2000)
    args = parser.parse_args()

    ranker = SavedRanker(args.models, threads=args.threads)
    dataset = ds.dataset(args.scored, format='parquet')
    table = dataset.to_table(filter=pc.field('txn_date') >= pc.scalar(args.tail_start))
    frame = table.to_pandas()
    print(f'Tail rows: {len(frame):,}  positives: {int(frame.is_laundering.sum()):,}', flush=True)

    scores = ranker.predict(frame)
    ranked = frame[['is_laundering']].assign(risk_score=scores).sort_values('risk_score', ascending=False)
    metrics = ranking_metrics(ranked, k=args.k)
    print('TAIL_GENERALIZATION', metrics, flush=True)


if __name__ == '__main__':
    main()
