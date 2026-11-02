import json
import re

import numpy as np
import pandas as pd
import pytest

import pyarrow.dataset as ds

from bda_pipeline.analytics import ranking_metrics
from bda_pipeline.model import (CANDIDATES, batch_iterator, choose_candidate, compare_models, digest, encode,
                                explain_queue, fit_encoder, positive_weight, rank, read_split, SavedRanker,
                                scan_split)
from bda_pipeline.splits import row_share_splits


def sample():
    size = 320
    return pd.DataFrame(dict(
        transaction_id=[f'HI-Small:{i}' for i in range(size)], source_row_index=range(size),
        timestamp=pd.to_datetime(['2022-09-01']*80 + ['2022-09-07']*80 +
                                 ['2022-09-09']*80 + ['2022-09-12']*80),
        from_bank='001', to_bank='1', from_account='000A', to_account='000A',
        payment_format=['ACH', 'Wire']*160, payment_currency='US Dollar',
        receiving_currency='US Dollar', amount_usd_asof=np.arange(size, dtype=float),
        txn_count_1h=1, txn_count_24h=2, txn_count_7d=3,
        distinct_counterparties_24h=1, distinct_counterparties_7d=1,
        in_txn_count_1h=1, in_txn_count_24h=2, in_txn_count_7d=3,
        distinct_senders_24h=1, distinct_senders_7d=1,
        is_laundering=[int(i % 7 == 0) for i in range(size)],
        risk_score=[50 if i % 2 == 0 else 0 for i in range(size)], risk_reasons='ACH'))


def test_features_ignore_labels_ids_future_categories_and_same_day_fx():
    frame = sample()
    vocabulary = fit_encoder(frame.iloc[:80])
    before = encode(frame, vocabulary)
    changed = frame.assign(is_laundering=1-frame.is_laundering, risk_score=999,
                           transaction_id='anything', source_row_index=999, amount_usd=1e99)
    pd.testing.assert_frame_equal(before, encode(changed, vocabulary))
    changed = frame.iloc[:1].assign(payment_format='FutureFormat')
    encoded = encode(changed, vocabulary)
    assert encoded.payment_format_unknown.iloc[0] == 1
    assert encoded.is_self_txn.iloc[0] == 1
    assert encoded.columns.tolist() == before.columns.tolist()
    assert 'FutureFormat' not in vocabulary['payment_format']
    with pytest.raises(ValueError, match='numeric'):
        encode(frame.assign(amount_usd_asof=np.inf), vocabulary)


def test_candidate_grid_is_declared_with_safe_names_and_resolvable_weights():
    names = [item['name'] for item in CANDIDATES]
    assert len(set(names)) == len(names)
    assert all(re.fullmatch(r'[a-z0-9-]+', name) for name in names)
    assert [positive_weight(item, 10, 1280) for item in CANDIDATES] == [1.0, 5.0, 128.0]
    assert all(positive_weight(item, 7, 700) > 0 for item in CANDIDATES)


def test_selection_and_ties_are_deterministic():
    candidates = [dict(name='b', metrics=dict(average_precision=.2, precision_at_k=.1)),
                  dict(name='a', metrics=dict(average_precision=.2, precision_at_k=.2))]
    assert choose_candidate(candidates)['name'] == 'a'
    ranked = rank(sample().iloc[:3].iloc[::-1], np.ones(3))
    assert ranked.source_row_index.tolist() == [0, 1, 2]


def test_frozen_outgoing_model_keeps_its_schema_after_incoming_features_added(tmp_path, monkeypatch):
    pytest.importorskip('xgboost')
    from bda_pipeline import model as module
    frame = sample()
    scored = tmp_path / 'reference.parquet'
    frame.to_parquet(scored, index=False)
    snapshot = tmp_path / 'dashboard.json'
    snapshot.write_text(json.dumps(dict(aml_scope='full', full_rows=len(frame),
        sources=[dict(file='bq1_scored.parquet', sha256=digest(scored))],
        aml={name: dict(metrics=dict(positives=int(frame.iloc[start:end].is_laundering.sum())))
             for name, start, end in [('validation',80,160), ('test',160,240), ('tail',240,320)]})))
    directory = tmp_path / 'legacy-model'
    with monkeypatch.context() as old:
        old.setattr(module, 'FEATURES', module.FEATURES[:5])
        compare_models(scored, snapshot, directory, rounds=3, threads=1)
        legacy = SavedRanker(directory, threads=1)
        expected = legacy.predict(frame)
    restored = SavedRanker(directory, threads=1)
    np.testing.assert_array_equal(restored.predict(frame), expected)
    queue = restored.queue(frame, expected)
    assert len(queue) == 100
    assert not any('incoming' in reason.lower() for reason in queue.model_evidence)
    schema = restored.feature_names + ['log1p_untrusted_feature']
    with pytest.raises(ValueError, match='frozen numeric feature schema'):
        encode(frame, restored.vocabulary, schema)


def test_tiny_model_comparison_artifact_reload_and_explanations(tmp_path, monkeypatch):
    xgb = pytest.importorskip('xgboost')
    frame = sample()
    scored = tmp_path / 'scored.parquet'
    frame.to_parquet(scored, index=False)
    snapshot = tmp_path / 'dashboard.json'
    snapshot.write_text(json.dumps(dict(aml_scope='full', full_rows=len(frame),
        sources=[dict(file='bq1_scored.parquet', sha256=digest(scored))],
        aml={name: dict(metrics=dict(positives=int(frame.iloc[start:end].is_laundering.sum())))
             for name, start, end in [('validation',80,160), ('test',160,240), ('tail',240,320)]})))
    output = tmp_path / 'model'
    from bda_pipeline import model as module
    original_read = module.read_split
    def guarded_read(dataset, name, splits):
        if name in ['test', 'tail']:
            assert (output / 'selection.json').exists()
        return original_read(dataset, name, splits)
    monkeypatch.setattr(module, 'read_split', guarded_read)
    report = compare_models(scored, snapshot, output, rounds=3, threads=1)
    assert report['total_rows'] == len(frame)
    assert report['total_positives'] == int(frame.is_laundering.sum())
    assert report['selection']['frozen_before_test']
    training_positives = int(frame.iloc[:80].is_laundering.sum())
    assert 'scale_pos_weight' not in report['parameters']
    selection = json.loads((output / 'selection.json').read_text())
    # Every candidate trained with its own weight, recorded in the selection and present in the saved model.
    weights = {item['name']: item['scale_pos_weight'] for item in selection['candidates']}
    assert weights == {'hist-depth5-unweighted': 1.0, 'hist-depth5-light': 5.0,
                       'hist-depth5-balanced': (80-training_positives)/training_positives}
    for item in selection['candidates']:
        config = json.loads(xgb.Booster(model_file=output / item['model_file']).save_config())
        assert float(config['learner']['objective']['reg_loss_param']['scale_pos_weight']) == pytest.approx(item['scale_pos_weight'])
    assert 'test' not in selection
    model = xgb.Booster(model_file=output / selection['selected']['model_file'])
    vocabulary = fit_encoder(frame.iloc[:80])
    scores = model.predict(xgb.DMatrix(encode(frame.iloc[160:240], vocabulary)))
    queue = explain_queue(model, frame.iloc[160:240], scores, vocabulary)
    assert 'is_laundering' not in queue.columns
    assert queue.model_score.is_monotonic_decreasing
    assert np.allclose(queue.model_score, 1/(1+np.exp(-queue.explanation_total_margin)))
    reloaded = SavedRanker(output, threads=1)
    assert np.allclose(reloaded.predict(frame.iloc[160:240].drop(columns=['is_laundering'])), scores)
    feature_path = output / 'features.json'
    original_features = feature_path.read_text()
    feature_path.write_text(original_features + ' ')
    with pytest.raises(ValueError, match='hash'):
        SavedRanker(output)
    feature_path.write_text(original_features)
    from bda_pipeline.model_dashboard import export_model_dashboard
    snapshot_data = json.loads(snapshot.read_text())
    snapshot_data['treasury'] = {'all': {'transactions': len(frame), 'volume_usd': 999}}
    snapshot_data['treasury_daily'] = [{'day': '2022-09-01', 'volume_usd': 999}]
    snapshot_data['diagnostics'] = {'variants': []}
    for period in ['validation', 'test', 'tail']:
        snapshot_data['aml'][period]['metrics'] = report['metrics'][period]['baseline']
    snapshot.write_text(json.dumps(snapshot_data))
    from pathlib import Path
    integrated = export_model_dashboard(scored, snapshot, output, tmp_path / 'dashboard',
                                        Path(__file__).resolve().parents[1] / 'dashboard')
    assert integrated['treasury'] == snapshot_data['treasury']
    assert integrated['treasury_daily'] == snapshot_data['treasury_daily']
    assert integrated['aml'] == snapshot_data['aml']
    for period, view in integrated['model']['views'].items():
        assert view['metrics'] == report['metrics'][period]['model']
        assert all('is_laundering' not in row for row in view['queue'])
        assert view['queue'][0]['from_bank'] == '001'
    cloud = tmp_path / 'verified-cloud'
    cloud.mkdir()
    cache = cloud / 'scores-by-source-index.npy'
    np.save(cache, reloaded.predict(frame).astype('float64'), allow_pickle=False)
    cloud_report = cloud / 'verification.json'
    cloud_report.write_text(json.dumps(dict(status='passed', scope='full-population-cloud-scoring',
        job_state='SUCCEEDED', success_marker_verified=True, rows=len(frame), job_run_id='test-cloud-run',
        source_sha256=digest(scored), model_sha256=selection['selected']['sha256'],
        score_cache=dict(file=cache.name, sha256=digest(cache)))))
    with monkeypatch.context() as guard:
        def forbidden_predict(*args):
            raise AssertionError('Dashboard must consume verified cloud scores without rescoring')
        guard.setattr(SavedRanker, 'predict', forbidden_predict)
        cloud_integrated = export_model_dashboard(scored, snapshot, output, tmp_path / 'cloud-dashboard',
            Path(__file__).resolve().parents[1] / 'dashboard', cloud_report)
    assert cloud_integrated['model']['inference_source'] == 'aws-glue'
    assert cloud_integrated['model']['provenance']['cloud_job_run_id'] == 'test-cloud-run'
    assert cloud_integrated['treasury'] == integrated['treasury']
    assert cloud_integrated['treasury_daily'] == integrated['treasury_daily']
    assert cloud_integrated['aml'] == integrated['aml']
    assert cloud_integrated['model']['views'] == integrated['model']['views']
    with pytest.raises(ValueError, match='fresh'):
        export_model_dashboard(scored, snapshot, output, tmp_path / 'dashboard', tmp_path)
    with pytest.raises(ValueError, match='fresh'):
        compare_models(scored, snapshot, output, rounds=3, threads=1)
    frame.to_parquet(scored, index=True)
    with pytest.raises(ValueError, match='hash'):
        compare_models(scored, snapshot, tmp_path / 'tampered', rounds=3, threads=1)


def test_training_uses_the_three_way_splits_recorded_in_the_snapshot(tmp_path):
    pytest.importorskip('xgboost')
    from bda_pipeline.splits import row_share_splits
    frame = sample()
    scored = tmp_path / 'scored.parquet'
    frame.to_parquet(scored, index=False)
    splits = json.loads(json.dumps(row_share_splits(frame.timestamp.dt.strftime('%Y-%m-%d').value_counts().to_dict())))
    assert splits == {'train': ['2022-09-01', '2022-09-09'], 'validation': ['2022-09-09', '2022-09-12'],
                      'test': ['2022-09-12', '2022-09-13']}
    snapshot = tmp_path / 'dashboard.json'
    snapshot.write_text(json.dumps(dict(aml_scope='full', full_rows=len(frame), splits=splits,
        sources=[dict(file='bq1_scored.parquet', sha256=digest(scored))],
        aml={name: dict(metrics=dict(positives=int(frame.iloc[start:end].is_laundering.sum())))
             for name, start, end in [('validation', 160, 240), ('test', 240, 320)]})))
    report = compare_models(scored, snapshot, tmp_path / 'model', rounds=3, threads=1)
    assert report['splits'] == splits
    assert set(report['metrics']) == {'validation', 'test'}
    assert report['train']['rows'] == 160 and report['total_rows'] == len(frame)
    assert SavedRanker(tmp_path / 'model').splits == splits


def streaming_sample(dense_days=12, per_day=500, tail_days=2, tail_rows=3, seed=7):
    """Several batches' worth of varied rows: dense days followed by a sparse tail, with labels that depend on the features."""
    rng = np.random.default_rng(seed)
    days = [pd.Timestamp('2022-09-01') + pd.Timedelta(days=i) for i in range(dense_days + tail_days)]
    sizes = [per_day] * dense_days + [tail_rows] * tail_days
    stamps = np.concatenate([day + pd.to_timedelta(rng.integers(0, 86400, size), unit='s') for day, size in zip(days, sizes)])
    size = len(stamps)
    amount = rng.lognormal(6, 1.5, size)
    count = rng.integers(0, 20, size)
    chance = 1 / (1 + np.exp(-(np.log(amount) - 9 + count / 4)))
    return pd.DataFrame(dict(
        transaction_id=[f'HI-Small:{i}' for i in range(size)], source_row_index=rng.permutation(size), timestamp=stamps,
        from_bank=rng.choice(['001', '002', '0003'], size), to_bank=rng.choice(['001', '2'], size),
        from_account=rng.choice(['A1', 'B2'], size), to_account=rng.choice(['A1', 'C3'], size),
        payment_format=rng.choice(['ACH', 'Wire', 'Cheque'], size), payment_currency=rng.choice(['US Dollar', 'Euro'], size),
        receiving_currency=rng.choice(['US Dollar', 'Euro'], size), amount_usd_asof=amount,
        txn_count_1h=count, txn_count_24h=count + 1, txn_count_7d=count + 2,
        distinct_counterparties_24h=rng.integers(0, 5, size), distinct_counterparties_7d=rng.integers(0, 9, size),
        in_txn_count_1h=rng.integers(0, 4, size), in_txn_count_24h=rng.integers(0, 8, size), in_txn_count_7d=rng.integers(0, 12, size),
        distinct_senders_24h=rng.integers(0, 4, size), distinct_senders_7d=rng.integers(0, 7, size),
        is_laundering=(rng.random(size) < chance).astype(int), risk_score=rng.integers(0, 100, size), risk_reasons='x'))


def write_run(tmp_path, frame, row_group_size=700):
    """Writes the frame as several row groups plus a snapshot whose splits come from its daily counts."""
    scored = tmp_path / 'scored.parquet'
    frame.to_parquet(scored, index=False, row_group_size=row_group_size)
    splits = json.loads(json.dumps(row_share_splits(frame.timestamp.dt.strftime('%Y-%m-%d').value_counts().to_dict())))
    aml = {name: dict(metrics=dict(positives=int(frame.is_laundering[(frame.timestamp >= start) & (frame.timestamp < end)].sum())))
           for name, (start, end) in splits.items() if name != 'train'}
    snapshot = tmp_path / 'dashboard.json'
    snapshot.write_text(json.dumps(dict(aml_scope='full', full_rows=len(frame), splits=splits, aml=aml,
                                        sources=[dict(file='bq1_scored.parquet', sha256=digest(scored))])))
    return scored, snapshot, splits


def collect(iterator):
    """Drains one pass of a DataIter the way XGBoost does, returning the concatenated features and labels."""
    parts = []
    iterator.reset()
    while iterator.next(lambda **kwargs: parts.append(kwargs)):
        pass
    return pd.concat([part['data'] for part in parts], ignore_index=True), np.concatenate([part['label'] for part in parts])


def test_streamed_batches_equal_the_loaded_split_encoding(tmp_path):
    pytest.importorskip('xgboost')
    scored, _, splits = write_run(tmp_path, streaming_sample())
    dataset = ds.dataset(scored)
    loaded = read_split(dataset, 'train', splits)
    counts = scan_split(dataset, 'train', splits, 400)
    vocabulary = counts['vocabulary']
    assert vocabulary == fit_encoder(loaded)
    assert (counts['rows'], counts['positives']) == (len(loaded), int(loaded.is_laundering.sum()))
    iterator = batch_iterator(dataset, 'train', splits, vocabulary, 400)
    matrix, labels = collect(iterator)
    # Two passes, as XGBoost makes: same rows in the same order, and no order error.
    again, _ = collect(iterator)
    pd.testing.assert_frame_equal(matrix, again)
    pd.testing.assert_frame_equal(matrix, encode(loaded, vocabulary).reset_index(drop=True))
    np.testing.assert_array_equal(labels, loaded.is_laundering.to_numpy())
    assert iterator.feature_names == matrix.columns.tolist()


def test_stream_rejects_changed_batch_order_and_bad_splits(tmp_path):
    pytest.importorskip('xgboost')
    frame = streaming_sample()
    scored, _, splits = write_run(tmp_path, frame)
    dataset = ds.dataset(scored)
    vocabulary = scan_split(dataset, 'train', splits, 400)['vocabulary']

    class Flaky:
        """Delegates to the dataset but reverses the batch order on the second scan."""
        scans = 0

        def to_batches(self, **kwargs):
            Flaky.scans += 1
            batches = list(dataset.to_batches(**kwargs))
            return iter(batches[::-1] if Flaky.scans > 1 else batches)

    iterator = batch_iterator(Flaky(), 'train', splits, vocabulary, 400)
    collect(iterator)
    with pytest.raises(ValueError, match='order changed'):
        collect(iterator)
    with pytest.raises(ValueError, match='Empty split'):
        scan_split(dataset, 'train', {'train': ('2030-01-01', '2030-01-02')}, 400)
    for label, changed, message in [('duplicated', frame.assign(source_row_index=0), 'Duplicate'),
                                    ('bad', frame.assign(is_laundering=2), 'invalid labels')]:
        folder = tmp_path / label
        folder.mkdir()
        path, _, folder_splits = write_run(folder, changed)
        with pytest.raises(ValueError, match=message):
            scan_split(ds.dataset(path), 'train', folder_splits, 400)


def in_memory_validation_metrics(scored, splits, rounds, threads=1):
    """The pre-streaming training path: both splits loaded and encoded whole. Returns validation metrics per candidate and the feature names."""
    import xgboost as xgb
    dataset = ds.dataset(scored)
    training, validation = read_split(dataset, 'train', splits), read_split(dataset, 'validation', splits)
    vocabulary = fit_encoder(training)
    xtrain, xvalid = encode(training, vocabulary), encode(validation, vocabulary)
    positives = int(training.is_laundering.sum())
    dtrain = xgb.QuantileDMatrix(xtrain, label=training.is_laundering, nthread=threads)
    dvalid = xgb.QuantileDMatrix(xvalid, label=validation.is_laundering, ref=dtrain, nthread=threads)
    params = dict(objective='binary:logistic', tree_method='hist', device='cpu', eval_metric='aucpr', eta=.1,
                  min_child_weight=10, subsample=1, colsample_bytree=1, seed=459, nthread=threads)
    metrics = {}
    for candidate in CANDIDATES:
        weight = positive_weight(candidate, positives, len(training) - positives)
        model = xgb.train({**params, 'max_depth': candidate['max_depth'], 'scale_pos_weight': weight}, dtrain,
                          num_boost_round=rounds, evals=[(dvalid, 'validation')], early_stopping_rounds=20, verbose_eval=False)
        model = model[:model.best_iteration + 1]
        metrics[candidate['name']] = ranking_metrics(rank(validation, model.predict(xgb.DMatrix(xvalid))))
    return metrics, xtrain.columns.tolist()


def test_streamed_training_tracks_the_in_memory_path_on_multi_batch_data(tmp_path):
    pytest.importorskip('xgboost')
    scored, snapshot, splits = write_run(tmp_path, streaming_sample())
    report = compare_models(scored, snapshot, tmp_path / 'streamed', rounds=40, threads=1, batch_rows=400)
    reference, names = in_memory_validation_metrics(scored, splits, rounds=40)
    assert report['feature_names'] == names and report['batch_rows'] == 400
    for item in report['selection']['candidates']:
        # Per-batch quantile sketches may move split points slightly, so compare ranking quality, not trees.
        assert item['metrics']['average_precision'] == pytest.approx(reference[item['name']]['average_precision'], abs=0.03)
        assert item['metrics']['rows'] == reference[item['name']]['rows']
    # Batch size changes the sketch, never the data seen.
    other = compare_models(scored, snapshot, tmp_path / 'other', rounds=40, threads=1, batch_rows=2500)
    assert other['train'] == report['train'] and other['total_rows'] == report['total_rows']


def test_windows_reaching_the_sparse_tail_report_dense_and_tail_metrics(tmp_path):
    pytest.importorskip('xgboost')
    scored, snapshot, _ = write_run(tmp_path, streaming_sample())
    report = compare_models(scored, snapshot, tmp_path / 'model', rounds=10, threads=1, batch_rows=400)
    assert report['sparse_tail'] == dict(fraction=0.01, start='2022-09-13', median_daily_rows=500.0)
    test = report['metrics']['test']
    assert test['tail']['model']['rows'] == 6 and test['dense']['model']['rows'] == test['model']['rows'] - 6
    assert test['tail']['baseline']['rows'] == 6
    # The headline numbers cover the whole window, and windows wholly before the tail have no breakdown.
    assert 'dense' not in report['metrics']['validation'] and 'tail' not in report['metrics']['validation']
    other = tmp_path / 'dense'
    other.mkdir()
    scored, snapshot, _ = write_run(other, streaming_sample(tail_days=0))
    assert compare_models(scored, snapshot, other / 'model', rounds=5, threads=1, batch_rows=400)['sparse_tail'] is None
