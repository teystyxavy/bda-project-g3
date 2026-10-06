"""Network-isolated AWS processing: source verification, baseline, reconciliation and release."""
import gc
import hashlib
import json
import shutil
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from .acquisition import validate_fx
from .analytics import add_behaviour, ranking_metrics
from .batch import COLUMNS, FEATURES, PERIODS, export_verified_batch
from .cloud_scores import checked_indices, feature_fingerprints
from .dashboard import records
from .inference import SCORING_COLUMNS, verify_artifacts
from .model import INPUT_COLUMNS, SavedRanker, digest, rank
from .release import prepare_release
from .transform import TRANSACTION_COLUMNS, IDENTIFIER_DTYPES
from .treasury_verification import expected_corridors, compare_corridors


def raw_fingerprints(frame):
    normalized = frame[TRANSACTION_COLUMNS].copy()
    normalized['timestamp'] = pd.to_datetime(normalized.timestamp).map(lambda t: pd.Timestamp(t).value)
    for name in ['amount_paid', 'amount_received']:
        normalized[name] = pd.to_numeric(normalized[name], errors='raise').astype('float64')
    normalized['is_laundering'] = pd.to_numeric(normalized.is_laundering, errors='raise').astype('int64')
    if normalized.isna().any().any():
        raise ValueError('Missing source field')
    return np.asarray([hashlib.sha256(json.dumps(row, separators=(',', ':'), allow_nan=False).encode()).digest()
                       for row in normalized.itertuples(index=False, name=None)], dtype='S32')


def verify_curated(curated, raw, manifest):
    raw = Path(raw)
    for name, info in manifest['files'].items():
        if digest(raw / name) != info['sha256'] or (raw / name).stat().st_size != info['bytes']:
            raise ValueError('Staged raw source hash/size mismatch: ' + name)
    rates = pd.read_csv(raw / 'fx.csv')
    validate_fx(rates, pd.Timestamp(manifest['start_date']).date() - pd.Timedelta(days=1),
                pd.Timestamp(manifest['end_date']).date(), manifest['currencies'])
    fx = {(str(day), str(currency)): float(value) for day, currency, value in
          rates[['txn_date', 'currency', 'usd_rate']].itertuples(index=False, name=None)}
    n = manifest['rows']
    raw_hashes = np.empty(n, dtype='S32')
    offset = positives_raw = 0
    dtype = {name: 'string' for name in TRANSACTION_COLUMNS if name not in ['amount_paid', 'amount_received', 'is_laundering']}
    for frame in pd.read_csv(raw / 'transactions.csv', header=0, names=TRANSACTION_COLUMNS,
                             dtype=dtype, chunksize=100_000):
        if offset + len(frame) > n:
            raise ValueError('Unexpected raw population')
        frame.timestamp = pd.to_datetime(frame.timestamp, format='%Y/%m/%d %H:%M', errors='raise')
        raw_hashes[offset:offset + len(frame)] = raw_fingerprints(frame)
        positives_raw += int(frame.is_laundering.sum())
        offset += len(frame)
    if offset != n:
        raise ValueError('Incomplete raw population')
    seen = np.zeros(n, dtype=bool)
    positives = 0
    amount_usd = 0.
    histories = []
    dataset = ds.dataset(curated, format='parquet', partitioning='hive', exclude_invalid_files=True)
    for batch in dataset.to_batches(batch_size=100_000):
        frame = batch.to_pandas()
        indices = checked_indices(frame, seen)
        if not np.array_equal(raw_hashes[indices], raw_fingerprints(frame)):
            raise ValueError('Curated transaction differs from original source')
        if not frame.txn_date.astype(str).eq(frame.timestamp.dt.strftime('%Y-%m-%d')).all():
            raise ValueError('Curated date partition mismatch')
        if frame[['from_entity_id', 'to_entity_id']].isna().any().any():
            raise ValueError('Incomplete account join')
        for column in ['amount_usd', 'amount_usd_asof', *FEATURES]:
            values = frame[column].to_numpy(dtype=float)
            if not np.isfinite(values).all() or (values < 0).any():
                raise ValueError('Invalid causal feature or USD value')
        for column, shift in [('amount_usd', 0), ('amount_usd_asof', 1)]:
            days = (frame.timestamp - pd.Timedelta(days=shift)).dt.strftime('%Y-%m-%d')
            expected = frame.amount_paid.to_numpy() * np.asarray([
                fx[(day, currency)] for day, currency in zip(days, frame.payment_currency)])
            np.testing.assert_allclose(frame[column], expected, rtol=1e-12, atol=1e-8)
        # Independent complete sender histories, chosen without labels; roughly 1%.
        bank = frame.from_bank.astype(str).str.lstrip('0').replace('', '0')
        keep = pd.Series([int(hashlib.sha256(f'{b}:{a}'.encode()).hexdigest()[:8], 16) % 100 == 0
                          for b, a in zip(bank, frame.from_account)], index=frame.index)
        if keep.any(): histories.append(frame.loc[keep, COLUMNS])
        positives += int(frame.is_laundering.sum())
        amount_usd += float(frame.amount_usd.sum())
    if not seen.all() or positives != positives_raw:
        raise ValueError('Curated census or label count differs from source')
    checked_rows = 0
    if histories:
        reference = pd.concat(histories, ignore_index=True).sort_values('source_row_index').reset_index(drop=True)
        actual = reference[FEATURES].copy()
        recalculated = add_behaviour(reference)
        np.testing.assert_array_equal(actual.to_numpy(), recalculated[FEATURES].to_numpy())
        checked_rows = len(reference)
    return {'status': 'passed', 'rows': n, 'positives': positives, 'amount_usd': amount_usd,
            'raw_identity_comparison': 'Every source row: ordered SHA256 fingerprint',
            'fx_comparison': 'All same-day and prior-day valuations',
            'behaviour_reference_rows': checked_rows,
            'behaviour_scope': 'Complete histories for deterministic label-independent approximately 1% sender cohort'}


def baseline_stage(inputs, output, assets):
    inputs, output = Path(inputs), Path(output)
    manifest = json.loads((inputs / 'run/manifest.json').read_text())
    output.mkdir(parents=True, exist_ok=True)
    proof = verify_curated(inputs / 'curated', inputs / 'raw', manifest)
    proof_path = output / 'proof.json'
    proof_path.write_text(json.dumps(proof, allow_nan=False))
    export_verified_batch(inputs / 'curated', proof_path, output / 'baseline', assets, serving_only=True)
    snapshot = output / 'baseline/dashboard/dashboard.json'
    data = json.loads(snapshot.read_text())
    data['provenance']['scoring_location'] = 'aws-sagemaker-processing rule baseline'
    snapshot.write_text(json.dumps(data, allow_nan=False))
    (output / 'manifest').mkdir()
    shutil.copyfile(snapshot, output / 'manifest/dashboard.json')
    provenance = {'rows': manifest['rows'], 'scored_sha256': digest(output / 'baseline/bq1_scored.parquet'),
                  'snapshot_sha256': digest(snapshot), 'job_name': 'is459-g3-train-' + manifest['run_id'],
                  'image_uri': json.loads((inputs / 'model/deployment.json').read_text())['image_uri'],
                  'status': 'prepared-not-submitted', 'model_promotion': False}
    (output / 'manifest/input.json').write_text(json.dumps(provenance))
    return proof


def verify_scores(reference, scored, model_dir, manifest, output):
    verify_artifacts(model_dir, manifest['model_manifest_sha256'])
    ranker = SavedRanker(model_dir)
    n = manifest['rows']
    seen = np.zeros(n, dtype=bool)
    fingerprints = np.empty(n, dtype='S32')
    for batch in ds.dataset(reference, format='parquet').to_batches(columns=SCORING_COLUMNS, batch_size=100_000):
        frame = batch.to_pandas()
        indices = checked_indices(frame, seen)
        for column, vocabulary in ranker.vocabulary.items():
            if not frame[column].isin(vocabulary).all():
                raise ValueError('Frozen model categorical compatibility failure: ' + column)
        fingerprints[indices] = feature_fingerprints(frame)
    if not seen.all(): raise ValueError('Incomplete baseline score reference')
    seen[:] = False
    scores = np.empty(n, dtype='float64')
    difference = 0.
    for batch in ds.dataset(scored, format='parquet', partitioning='hive', exclude_invalid_files=True).to_batches(batch_size=100_000):
        frame = batch.to_pandas()
        if set(frame.columns) != set(SCORING_COLUMNS) | {'model_score', 'txn_date'}:
            raise ValueError('Unexpected scoring columns or evaluation labels')
        indices = checked_indices(frame, seen)
        if not np.array_equal(fingerprints[indices], feature_fingerprints(frame)):
            raise ValueError('Scored feature mismatch')
        actual = frame.model_score.to_numpy(dtype='float64')
        if not np.isfinite(actual).all() or ((actual < 0) | (actual > 1)).any():
            raise ValueError('Invalid model ranking score')
        expected = ranker.predict(frame)
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-7)
        difference = max(difference, float(np.abs(actual - expected).max()))
        scores[indices] = actual
    if not seen.all(): raise ValueError('Incomplete cloud scores')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    np.save(output / 'scores.npy', scores, allow_pickle=False)
    return scores, {'status': 'passed', 'rows': n, 'max_absolute_score_difference': difference,
                    'labels_excluded': True, 'model_sha256': ranker.selection['selected']['sha256']}


def export_stage(inputs, output, assets):
    inputs, output = Path(inputs), Path(output)
    manifest = json.loads((inputs / 'run/manifest.json').read_text())
    reference = inputs / 'baseline/baseline/bq1_scored.parquet'
    reference_hash = digest(reference)
    output.mkdir(parents=True, exist_ok=True)
    scores, score_proof = verify_scores(reference, inputs / 'scored', inputs / 'model', manifest, output / 'evidence')
    csv_files = list((inputs / 'athena').rglob('*.csv'))
    if len(csv_files) != 1: raise ValueError('Require exactly one Athena result CSV')
    actual = pd.read_csv(csv_files[0], dtype={column: 'string' for column in ['corridor_type', 'txn_date', 'from_node', 'to_node']})
    dataset = ds.dataset(reference, format='parquet')
    batches = (batch.to_pandas().assign(txn_date=lambda frame: frame.txn_date.astype(str))
               for batch in dataset.to_batches(columns=['txn_date', 'from_bank', 'to_bank',
                    'payment_currency', 'receiving_currency', 'amount_usd'], batch_size=100_000))
    corridor_proof = compare_corridors(expected_corridors(batches, manifest['rows']), actual, manifest['rows'])
    (output / 'evidence/scores.json').write_text(json.dumps(score_proof, allow_nan=False))
    (output / 'evidence/corridors.json').write_text(json.dumps(corridor_proof, allow_nan=False))
    ranker = SavedRanker(inputs / 'model')
    snapshot = json.loads((inputs / 'baseline/manifest/dashboard.json').read_text())
    views = {}
    for name, (start, end) in PERIODS.items():
        condition = (ds.field('timestamp') >= pd.Timestamp(start)) & (ds.field('timestamp') < pd.Timestamp(end))
        frame = dataset.to_table(columns=INPUT_COLUMNS, filter=condition).to_pandas()
        if frame.empty:
            views[name] = {'metrics': snapshot['aml'][name]['metrics'], 'queue': []}
        else:
            values = scores[frame.source_row_index.to_numpy(dtype='int64')]
            metrics_frame = frame[['timestamp', 'source_row_index', 'is_laundering']]
            views[name] = {'metrics': ranking_metrics(rank(metrics_frame, values)), 'queue': records(ranker.queue(frame, values))}
        del frame
        gc.collect()
    snapshot['model'] = {'name': ranker.selection['selected']['name'], 'views': views,
        'inference_source': 'aws-glue', 'validation_gate_passed': ranker.selection['validation_gate_passed'],
        'selection_policy': ranker.selection['selection_policy'],
        'score_definition': 'Uncalibrated ranking score; active frozen model, not a newly promoted candidate.',
        'explanation_definition': 'Signed TreeSHAP margins reconstructed in AWS processing; not causal evidence.',
        'provenance': {'model_sha256': ranker.selection['selected']['sha256'],
                       'source_sha256': reference_hash, 'training_source_sha256': ranker.selection['source_sha256']}}
    snapshot['pipeline'] = {key: manifest[key] for key in ['run_id', 'dataset_version', 'subset', 'source_scope',
                             'start_date', 'end_date', 'processing_sha256', 'deployment_sha256', 'train']}
    snapshot['pipeline']['fx_sha256'] = manifest['files']['fx.csv']['sha256']
    snapshot['pipeline']['refresh_time'] = datetime.now(timezone.utc).isoformat()
    training_proof = None
    if manifest['train']:
        archives = list((inputs / 'candidate').rglob('model.tar.gz'))
        if len(archives) != 1: raise ValueError('Require one candidate archive')
        candidate = output / 'candidate'
        candidate.mkdir()
        with tarfile.open(archives[0], 'r:gz') as archive:
            members = archive.getmembers()
            if len(members) > 30 or sum(item.size for item in members) > 200_000_000:
                raise ValueError('Oversized candidate archive')
            names = set()
            for member in members:
                if member.isdir() and member.name in ['.', './']: continue
                name = member.name.removeprefix('./')
                if (not member.isfile() or '/' in name or '\\' in name or name in names
                        or name in ['', '.', '..'] or not name.endswith(('.json', '.ubj', '.csv'))):
                    raise ValueError('Unsafe candidate archive')
                names.add(name)
                with archive.extractfile(member) as source, (candidate / name).open('wb') as target:
                    shutil.copyfileobj(source, target)
        candidate_ranker = SavedRanker(candidate)
        report = json.loads((candidate / 'comparison.json').read_text())
        execution = report['execution']
        image = json.loads((inputs / 'model/deployment.json').read_text())['image_uri']
        if (report['total_rows'] != manifest['rows'] or execution['job_name'] != 'is459-g3-train-' + manifest['run_id']
                or execution['image_uri'] != image or execution['scored_sha256'] != reference_hash
                or candidate_ranker.selection['source_sha256'] != reference_hash
                or execution['promotion'] != 'candidate-only'):
            raise ValueError('Candidate provenance mismatch')
        for name, (start, end) in PERIODS.items():
            frame = dataset.to_table(columns=INPUT_COLUMNS, filter=(ds.field('timestamp') >= pd.Timestamp(start)) & (ds.field('timestamp') < pd.Timestamp(end))).to_pandas()
            values = candidate_ranker.predict(frame)
            metrics_frame = frame[['timestamp', 'source_row_index', 'is_laundering']]
            for kind, metrics in [('model', ranking_metrics(rank(metrics_frame, values))),
                                  ('baseline', ranking_metrics(rank(metrics_frame, frame.risk_score.to_numpy())))]:
                reviewed = report['metrics'][name][kind]
                if set(metrics) != set(reviewed): raise ValueError('Candidate metric schema mismatch')
                for key, actual in metrics.items():
                    if actual is None:
                        if reviewed[key] is not None: raise ValueError('Candidate metric mismatch')
                    elif not np.isclose(actual, reviewed[key], rtol=1e-9, atol=1e-12):
                        raise ValueError('Candidate metric mismatch')
            del frame, values
            gc.collect()
        training_proof = {'status': 'passed', 'candidate_only': True, 'model_promoted': False,
            'archive_sha256': digest(archives[0]), 'validation_gate_passed': candidate_ranker.selection['validation_gate_passed']}
        snapshot['pipeline']['training'] = training_proof
    snapshot['provenance']['scoring_location'] = 'AWS Glue scoring, Athena treasury verification and SageMaker Processing export'
    snapshot['buildStatus'] = 'complete'
    snapshot['generated_at'] = snapshot['pipeline']['refresh_time']
    site = output / 'dashboard'
    site.mkdir()
    (site / 'dashboard.json').write_text(json.dumps(snapshot, allow_nan=False))
    for name in ['index.html', 'dashboard.js', 'dashboard.css']: shutil.copyfile(Path(assets) / name, site / name)
    release = prepare_release(site, output / 'release')
    release['pipeline_run_id'] = manifest['run_id']
    release['processing_sha256'] = manifest['processing_sha256']
    release['verification'] = {'scores': score_proof, 'corridors': corridor_proof}
    if training_proof: release['verification']['training'] = training_proof
    (output / 'release/manifest.json').write_text(json.dumps(release, allow_nan=False))
    return release
