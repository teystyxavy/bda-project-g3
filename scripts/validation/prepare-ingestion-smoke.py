"""Prepare a 1,000-row local fixture for the Glue reference calculations.

No AWS calls. With --stage prepare, builds input channels from existing local
HI-Small files. --stage baseline runs the same baseline function as Glue;
--stage finish adds native frozen scores and independent corridor reference;
--stage export runs the same export function as Glue. These are local numerical
checks, not proof of S3/IAM or a deployed Glue run.

All stages take --output, --deployment, --models and --fx-file. Execute them in
order: prepare, baseline, finish, export. Use the offline Glue container regression
script to check runtime compatibility as well.
"""
import argparse
import json
import shutil
from pathlib import Path
import pandas as pd
from bda_pipeline.acquisition import prepare_inputs
from bda_pipeline.analytics import add_behaviour
from bda_pipeline.ingest_contract import sha
from bda_pipeline.ingest_compute import baseline_stage, export_stage
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
    parser.add_argument('--stage', choices=['prepare', 'baseline', 'finish', 'export'], default='prepare')
    args = parser.parse_args()
    # Same folder name the Processing container reads its inputs from.
    inputs = args.output / 'input'
    if args.stage in ['baseline', 'export']:
        runner = baseline_stage if args.stage == 'baseline' else export_stage
        runner(inputs, args.output / (args.stage + '-output'), Path(__file__).resolve().parents[2] / 'dashboard')
        print('Completed local ' + args.stage + '; no AWS job started')
        return
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
        print('Prepared 1,000 real transactions; next run --stage baseline')
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
        print('Prepared native scoring and independent corridor reference; next run --stage export')


if __name__ == '__main__': main()
