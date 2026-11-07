"""Frozen-model evaluation of explicitly selected, previously verified S3 partitions."""
import json
import shutil
from pathlib import Path

from .batch import daily_row_counts, export_verified_batch
from .ingest_contract import sha
from .model import digest
from .selected_population import validate_selection


def build_baseline(inputs, output, assets):
    """Reconcile selected rows/labels/USD against independent source proof; freeze rule thresholds."""
    inputs, output = Path(inputs), Path(output)
    manifest = json.loads((inputs / 'run/manifest.json').read_text())
    days = validate_selection(manifest)
    source = json.loads((inputs / 'source/manifest.json').read_text())
    proof = json.loads((inputs / 'source/proof.json').read_text())
    training_bytes = (inputs / 'source/training-dashboard.json').read_bytes()
    training = json.loads(training_bytes)
    import hashlib
    selection = manifest['selection']
    if (sha(source) != selection['source_manifest_sha256'] or sha(proof) != selection['source_proof_sha256']
            or hashlib.sha256(training_bytes).hexdigest() != selection['training_snapshot_sha256']
            or proof.get('status') != 'passed' or proof['rows'] != manifest['source_index_rows']
            or source['subset'] != manifest['subset'] or source['files'] != manifest['files']
):
        raise ValueError('Selected source provenance mismatch')
    # Evaluation begins after validation; previous test dates are reported separately.
    report = json.loads((inputs / 'model/comparison.json').read_text())
    if days[0] < report['splits']['validation'][1]:
        raise ValueError('Evaluation dates overlap model training or validation')
    import pyarrow.dataset as ds
    counts = daily_row_counts(ds.dataset(inputs / 'curated', format='parquet', partitioning='hive'))
    if set(counts) != set(days) or any(counts[d] != proof['daily'][d]['rows'] for d in days):
        raise ValueError('Selected daily population mismatch')
    expected = sum(proof['daily'][day]['rows'] for day in days)
    if expected != manifest['rows']:
        raise ValueError('Selected row total mismatch')
    # Model development reference is immutable; no October rows fit thresholds or select a model.
    model_report = json.loads((inputs / 'model/comparison.json').read_text())
    if model_report['execution']['snapshot_sha256'] != selection['training_snapshot_sha256']:
        raise ValueError('Model training-reference mismatch')
    output.mkdir(parents=True)
    (output / 'source-proof.json').write_text(json.dumps(proof))
    from datetime import date, timedelta
    end = (date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()
    splits = {**model_report['splits'], 'test': (days[0], end)}
    export_verified_batch(inputs / 'curated', output / 'source-proof.json', output / 'baseline', assets,
        serving_only=True, subset=manifest['subset'], end_date=days[-1], splits=splits, dates=days,
        frozen_thresholds=training['provenance']['training_p99'])
    snapshot_path = output / 'baseline/dashboard/dashboard.json'
    snapshot = json.loads(snapshot_path.read_text())
    snapshot['splits'] = {name: window for name, window in splits.items() if name in ['train', 'test']}
    snapshot['aml'].pop('validation', None)
    snapshot['aml_scope'] = 'selected-population'
    snapshot['source_index_rows'] = manifest['source_index_rows']
    snapshot['population'] = {**selection, 'rows': expected, 'source_subset': manifest['subset'],
        'network_rows': source['rows'], 'network_start_date': source['start_date'],
        'network_end_date': source['end_date'], 'history_policy': 'Features retain verified history from the full source. BQ1 covers selected evaluation dates; BQ2 covers the complete source population.'}
    subset_proof = {'status': 'passed', 'rows': expected, 'days': len(days),
        'positives': sum(proof['daily'][d]['positives'] for d in days),
        'amount_usd': sum(proof['daily'][d]['amount_usd'] for d in days),
        'source_proof_sha256': sha(proof), 'source_manifest_sha256': sha(source)}
    snapshot['provenance']['subset_verification'] = subset_proof
    identifiers = {row['transaction_id'].split(':')[0] for view in training['aml'].values()
                   for row in view.get('queue', []) if row.get('transaction_id')}
    if not identifiers and model_report.get('training_subset'):
        identifiers.add(model_report['training_subset'])
    if len(identifiers) != 1:
        raise ValueError('Ambiguous model training subset')
    snapshot['provenance']['model_training_subset'] = identifiers.pop()
    snapshot['provenance']['scoring_location'] = 'AWS Glue frozen-threshold subset evaluation'
    snapshot_path.write_text(json.dumps(snapshot, separators=(',', ':'), allow_nan=False))
    (output / 'manifest').mkdir()
    shutil.copyfile(snapshot_path, output / 'manifest/dashboard.json')
    (output / 'manifest/input.json').write_text(json.dumps({'rows': expected, 'scored_sha256': digest(output / 'baseline/bq1_scored.parquet')}))
    (output / 'proof.json').write_text(json.dumps(subset_proof))
    return subset_proof


def evaluation_breakdown(dataset, scores, ranker, development_dates, subset):
    """Exact separate metrics for prior test dates versus dates absent from development."""
    from .dashboard_exploration import RankedPopulation
    from .model import INPUT_COLUMNS
    groups = {name: RankedPopulation(100, True, subset) for name in ['previous_test_dates', 'unseen_dates']}
    development_dates = set(development_dates)
    for batch in dataset.to_batches(columns=INPUT_COLUMNS, batch_size=100_000):
        frame = batch.to_pandas()
        previous = frame.timestamp.dt.strftime('%Y-%m-%d').isin(development_dates)
        for name, selected in [('previous_test_dates', frame.loc[previous]), ('unseen_dates', frame.loc[~previous])]:
            groups[name].add(selected, scores[selected.source_row_index.to_numpy(dtype='int64')])
    return {name: {'metrics': group.finish()['metrics_by_k']['100'],
                   'definition': ('Dates previously included in the frozen model test report; not new holdout evidence.'
                      if name == 'previous_test_dates' else 'Dates absent from model training, validation and original test inputs; no retuning.')}
            for name, group in groups.items()}
