"""Prepare a real-data CPU-container smoke fixture locally; no AWS calls."""
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--fx-file', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, default=Path('dataset'))
    parser.add_argument('--stage', choices=['prepare', 'finish'], default='prepare')
    args = parser.parse_args()
    inputs = args.output / 'input'
    if args.stage == 'prepare':
        if args.output.exists(): raise ValueError('Use a fresh smoke directory')
        info = prepare_inputs(args.dataset / 'HI-Small_Trans.csv', args.dataset / 'HI-Small_accounts.csv', inputs / 'raw', rows=1000, fx_file=args.fx_file)
        config = json.loads(args.deployment.read_text())
        manifest = {**info, 'schema_version': 1, 'run_id': 'local-real-smoke', 'subset': 'HI-Small',
            'dataset_version': None, 'source_scope': 'smoke-prefix', 'train': False, 'rerun': True,
            'deployment_sha256': sha(config), 'model_manifest_sha256': config['model_manifest_sha256']}
        manifest['processing_sha256'] = sha({key: manifest[key] for key in ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version', 'deployment_sha256', 'train']})
        (inputs / 'run').mkdir()
        (inputs / 'run/manifest.json').write_text(json.dumps(manifest))
        shutil.copytree(args.models, inputs / 'model')
        shutil.copyfile(args.deployment, inputs / 'model/deployment.json')
        frame = build_curated_transactions(read_transactions(inputs / 'raw/transactions.csv'), read_accounts(inputs / 'raw/accounts.csv'), pd.read_csv(inputs / 'raw/fx.csv'))
        frame['transaction_id'] = 'HI-Small:' + frame.source_row_index.astype(str)
        previous = pd.read_csv(inputs / 'raw/fx.csv')
        lookup = {(r.txn_date, r.currency): r.usd_rate for r in previous.itertuples()}
        frame['amount_usd_asof'] = frame.amount_paid * pd.Series([lookup[(str((stamp-pd.Timedelta(days=1)).date()), currency)] for stamp, currency in zip(frame.timestamp, frame.payment_currency)], index=frame.index)
        frame = add_behaviour(frame)
        (inputs / 'curated').mkdir()
        frame.to_parquet(inputs / 'curated/data.parquet', index=False)
        (args.output / 'baseline-output').mkdir()
        print('Prepared 1,000 real transactions; next run the baseline container')
    else:
        shutil.copytree(args.output / 'baseline-output', inputs / 'baseline')
        frame = pd.read_parquet(inputs / 'baseline/baseline/bq1_scored.parquet')
        ranker = SavedRanker(inputs / 'model')
        cloud = frame[SCORING_COLUMNS].assign(model_score=ranker.predict(frame), txn_date=frame.txn_date)
        (inputs / 'scored').mkdir()
        cloud.to_parquet(inputs / 'scored/data.parquet', index=False)
        (inputs / 'athena').mkdir()
        expected_corridors([frame.assign(txn_date=frame.txn_date.astype(str))], len(frame)).to_csv(inputs / 'athena/result.csv', index=False)
        (args.output / 'export-output').mkdir()
        print('Prepared native scoring and independent corridor reference; next run the export container')


if __name__ == '__main__': main()
