"""Prepare a real-data CPU-container smoke fixture locally; no AWS calls.

Purpose
-------
In the cloud ingestion workflow, two SageMaker Processing jobs run the
pipeline worker container (``sagemaker/``): a *baseline* stage (verify the
curated data, build the rule-baseline/BQ1 reference and dashboard snapshot)
and an *export* stage (reconcile Glue scores and Athena corridors, then
build the release assets). This script builds, on a laptop, the same input
folder layout those containers expect, using 1,000 real HI-Small
transactions, so the container can be tested locally with Docker before
spending money on AWS.

It is run in two stages around two local container runs:

1. ``--stage prepare`` creates ``<output>/input/`` with
   ``raw/`` (transactions, accounts, fx via ``prepare_inputs``),
   ``run/manifest.json`` (a run manifest like the control Lambda writes),
   ``model/`` (frozen model + ``deployment.json``) and
   ``curated/data.parquet`` (curated rows with behaviour features computed by
   the local pandas reference), plus an empty ``<output>/baseline-output``.
   -> next, run the baseline container with ``baseline-output`` as output.
2. ``--stage finish`` copies the baseline output into ``input/baseline``,
   stands in for the Glue scoring job (native model scores ->
   ``input/scored/data.parquet``) and for Athena (independently computed
   corridor CSV -> ``input/athena/result.csv``), and creates an empty
   ``<output>/export-output``.
   -> next, run the export container.

Usage::

    python scripts/validation/prepare-ingestion-smoke.py --output output/ingest-smoke \\
        --deployment deployment.json --models <model dir> --fx-file <fx.csv> [--stage prepare]
    # ... run baseline container ...
    python scripts/validation/prepare-ingestion-smoke.py ... --stage finish
"""
import argparse
import json
import shutil
from pathlib import Path
import pandas as pd
from bda_pipeline.acquisition import prepare_inputs
from bda_pipeline.analytics import add_behaviour
from bda_pipeline.ingest_contract import sha
from bda_pipeline.ingest_compute import baseline_stage
from bda_pipeline.inference import SCORING_COLUMNS
from bda_pipeline.model import SavedRanker
from bda_pipeline.transform import read_transactions, read_accounts, build_curated_transactions
from bda_pipeline.treasury_verification import expected_corridors


def main():
    """Run the 'prepare' or 'finish' stage described in the module docstring."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--fx-file', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=Path('dataset'))
    parser.add_argument('--stage', choices=['prepare', 'finish'], default='prepare')
    args = parser.parse_args()
    # Same folder name the Processing container reads its inputs from.
    inputs = args.output / 'input'
    if args.stage == 'prepare':
        # Fresh directory so no old outputs can make the smoke test pass falsely.
        if args.output.exists(): raise ValueError('Use a fresh smoke directory')
        # Copy the first 1,000 rows + accounts + FX into input/raw and hash them.
        info = prepare_inputs(args.dataset / 'HI-Small_Trans.csv', args.dataset / 'HI-Small_accounts.csv', inputs / 'raw', rows=1000, fx_file=args.fx_file)
        config = json.loads(args.deployment.read_text())
        # Mirror the real run manifest. sha() = SHA-256 of canonical JSON, so the
        # container can check the deployment config and processing identity.
        manifest = {**info, 'schema_version': 1, 'run_id': 'local-real-smoke', 'subset': 'HI-Small',
            'dataset_version': None, 'source_scope': 'smoke-prefix', 'train': False, 'rerun': True,
            'deployment_sha256': sha(config), 'model_manifest_sha256': config['model_manifest_sha256']}
        # processing_sha256 identifies "what would be computed" (the stage-cache
        # key in the cloud): same subset/rows/files/dates/config => same hash.
        manifest['processing_sha256'] = sha({key: manifest[key] for key in ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version', 'deployment_sha256', 'train']})
        (inputs / 'run').mkdir()
        (inputs / 'run/manifest.json').write_text(json.dumps(manifest))
        # Frozen model files and the deployment config the container validates.
        shutil.copytree(args.models, inputs / 'model')
        shutil.copyfile(args.deployment, inputs / 'model/deployment.json')
        # Build the curated input locally (stands in for the Glue curation job).
        frame = build_curated_transactions(read_transactions(inputs / 'raw/transactions.csv'), read_accounts(inputs / 'raw/accounts.csv'), pd.read_csv(inputs / 'raw/fx.csv'))
        frame['transaction_id'] = 'HI-Small:' + frame.source_row_index.astype(str)
        # Prior-day FX conversion (amount_usd_asof): look up the rate for the
        # day before each transaction, i.e. a rate known at transaction time.
        previous = pd.read_csv(inputs / 'raw/fx.csv')
        lookup = {(r.txn_date, r.currency): r.usd_rate for r in previous.itertuples()}
        frame['amount_usd_asof'] = frame.amount_paid * pd.Series([lookup[(str((stamp-pd.Timedelta(days=1)).date()), currency)] for stamp, currency in zip(frame.timestamp, frame.payment_currency)], index=frame.index)
        # Trailing 1h/24h/7d counts and distinct counterparties per sender.
        frame = add_behaviour(frame)
        (inputs / 'curated').mkdir()
        frame.to_parquet(inputs / 'curated/data.parquet', index=False)
        (args.output / 'baseline-output').mkdir()
        print('Prepared 1,000 real transactions; next run the baseline container')
    else:
        # Stage 2: the baseline container's output becomes an input of export.
        shutil.copytree(args.output / 'baseline-output', inputs / 'baseline')
        frame = pd.read_parquet(inputs / 'baseline/baseline/bq1_scored.parquet')
        # Stand-in for the Glue scoring job: score with the frozen model natively.
        ranker = SavedRanker(inputs / 'model')
        cloud = frame[SCORING_COLUMNS].assign(model_score=ranker.predict(frame), txn_date=frame.txn_date)
        (inputs / 'scored').mkdir()
        cloud.to_parquet(inputs / 'scored/data.parquet', index=False)
        # Stand-in for the Athena BQ2 query: an independent pandas corridor
        # computation in the CSV format Athena would write.
        (inputs / 'athena').mkdir()
        expected_corridors([frame.assign(txn_date=frame.txn_date.astype(str))], len(frame)).to_csv(inputs / 'athena/result.csv', index=False)
        (args.output / 'export-output').mkdir()
        print('Prepared native scoring and independent corridor reference; next run the export container')


if __name__ == '__main__': main()
