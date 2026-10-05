import json

import numpy as np
import pandas as pd
import pytest

from bda_pipeline.model import (choose_candidate, compare_models, digest, encode,
                                explain_queue, fit_encoder, rank, SavedRanker)


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


def test_selection_and_ties_are_deterministic():
    candidates = [dict(name='b', metrics=dict(average_precision=.2, precision_at_k=.1)),
                  dict(name='a', metrics=dict(average_precision=.2, precision_at_k=.2))]
    assert choose_candidate(candidates)['name'] == 'a'
    ranked = rank(sample().iloc[:3].iloc[::-1], np.ones(3))
    assert ranked.source_row_index.tolist() == [0, 1, 2]


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
    def guarded_read(dataset, name):
        if name in ['test', 'tail']:
            assert (output / 'selection.json').exists()
        return original_read(dataset, name)
    monkeypatch.setattr(module, 'read_split', guarded_read)
    report = compare_models(scored, snapshot, output, rounds=3, threads=1)
    assert report['total_rows'] == len(frame)
    assert report['total_positives'] == int(frame.is_laundering.sum())
    assert report['selection']['frozen_before_test']
    training_positives = int(frame.iloc[:80].is_laundering.sum())
    assert report['parameters']['scale_pos_weight'] == (80-training_positives)/training_positives
    selection = json.loads((output / 'selection.json').read_text())
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
    with pytest.raises(ValueError, match='fresh'):
        export_model_dashboard(scored, snapshot, output, tmp_path / 'dashboard', tmp_path)
    with pytest.raises(ValueError, match='fresh'):
        compare_models(scored, snapshot, output, rounds=3, threads=1)
    frame.to_parquet(scored, index=True)
    with pytest.raises(ValueError, match='hash'):
        compare_models(scored, snapshot, tmp_path / 'tampered', rounds=3, threads=1)
