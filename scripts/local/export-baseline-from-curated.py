"""Produce bq1_scored.parquet + dashboard.json locally from an already-curated Parquet directory.

Where this sits in the pipeline
-------------------------------
The cloud "baseline" Glue job (``bda_pipeline.batch.export_verified_batch``, via
``ingest_compute.baseline_stage``) does two things before scoring: (1)
``verify_curated`` cross-checks the curated Parquet against the raw CSVs, and
(2) asserts the resulting ``proof.json``. For a local training experiment where
Curate/Score already succeeded in AWS and the curated Parquet was simply
downloaded, step (1) is unnecessary overhead (this produces a candidate model
for local evaluation, not a promotable artifact) - this script replaces it with
a self-asserted proof computed directly from the curated data (the same
rows/positives/amount_usd a real ``verify_curated`` would report, just not
cross-checked against the raw CSVs), then calls the same ``export_verified_batch``
production uses to write ``bq1_scored.parquet`` and ``dashboard.json`` - exactly
the inputs ``scripts/modeling/compare-local-model.py`` expects.

Usage
-----
    python scripts/local/export-baseline-from-curated.py \\
        --curated /path/to/curated --output /path/to/output \\
        --subset HI-Large --end-date 2023-01-12
"""
import argparse
import json
from pathlib import Path

import pyarrow.dataset as ds

from bda_pipeline.batch import export_verified_batch


def proof_from_curated(dataset):
    """One streaming pass over ``is_laundering``/``amount_usd`` to self-assert a proof.json.
    Not a substitute for ``verify_curated``'s cross-check against the raw CSVs - only safe for local experiments."""
    rows = positives = 0
    amount_usd = 0.0
    for batch in dataset.to_batches(columns=['is_laundering', 'amount_usd'], batch_size=1_000_000):
        frame = batch.to_pandas()
        rows += len(frame)
        positives += int(frame.is_laundering.sum())
        amount_usd += float(frame.amount_usd.sum())
        print(f'Counted {rows:,} rows', flush=True)
    return {'status': 'passed', 'rows': rows, 'positives': positives, 'amount_usd': amount_usd}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--curated', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing directories are rejected')
    parser.add_argument('--subset', default='HI-Large')
    parser.add_argument('--end-date', default='2023-01-12')
    args = parser.parse_args()
    dataset = ds.dataset(args.curated, format='parquet', partitioning='hive', exclude_invalid_files=True)
    proof = proof_from_curated(dataset)
    args.output.mkdir(parents=True)
    proof_path = args.output / 'proof.json'
    proof_path.write_text(json.dumps(proof), encoding='utf-8')
    # assets is never read: export_verified_batch only touches it when serving_only=False.
    report = export_verified_batch(args.curated, proof_path, args.output / 'baseline', args.output,
                                   serving_only=True, subset=args.subset, end_date=args.end_date)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
