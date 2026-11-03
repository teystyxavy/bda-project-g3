import json
from pathlib import Path

import pandas as pd
import pytest

from bda_pipeline.analytics import evaluate_baseline
from bda_pipeline.batch import AmlSummary, export_verified_batch
from bda_pipeline.dashboard import aml_view
from bda_pipeline.splits import LEGACY_SPLITS


def fixture():
    n = 240
    return pd.DataFrame({
        'source_row_index': range(n), 'transaction_id': [f'HI-Small:{i}' for i in range(n)],
        'timestamp': pd.to_datetime(['2022-09-01']*60 + ['2022-09-07']*60 + ['2022-09-09']*60 + ['2022-09-12']*60),
        'from_bank': '020', 'from_account': '0001', 'to_bank': '021', 'to_account': '0002',
        'payment_format': ['ACH', 'Wire']*(n//2), 'payment_currency': 'US Dollar', 'receiving_currency': 'US Dollar',
        'amount_usd': [float(i+1) for i in range(n)], 'amount_usd_asof': [float(i+1) for i in range(n)],
        'is_laundering': [int(i % 17 == 0) for i in range(n)],
        'txn_count_1h': [i % 14 for i in range(n)], 'txn_count_24h': 20, 'txn_count_7d': 30,
        'distinct_counterparties_24h': 5, 'distinct_counterparties_7d': 8,
        'amount_sum_24h': 100., 'amount_sum_7d': 150.,
        'in_txn_count_1h': [i % 9 for i in range(n)], 'in_txn_count_24h': 12, 'in_txn_count_7d': 18,
        'distinct_senders_24h': 3, 'distinct_senders_7d': 6,
        'in_amount_sum_24h': 100., 'in_amount_sum_7d': 150., 'corridor_prior_count': 2,
    }).assign(txn_date=lambda f: f.timestamp.dt.strftime('%Y-%m-%d'))


def test_streaming_summary_matches_exact_metrics_queue_and_ties():
    scored, _, _ = evaluate_baseline(fixture())
    summary = AmlSummary()
    for batch in [scored.iloc[100:], scored.iloc[:50], scored.iloc[50:100]]:
        summary.add(batch)
    actual, expected = summary.view(), aml_view(scored)
    assert actual['queue'] == expected['queue']
    assert actual['daily'] == expected['daily']
    assert actual['histogram'] == expected['histogram']
    assert actual['metrics'] == pytest.approx(expected['metrics'])


def test_full_export_matches_baseline_and_does_not_expose_labels(tmp_path):
    frame = fixture()
    curated = tmp_path / 'input.parquet'
    frame.to_parquet(curated, index=False)
    verification = tmp_path / 'verified.json'
    verification.write_text(json.dumps(dict(status='passed', rows=len(frame), positives=int(frame.is_laundering.sum()), amount_usd=float(frame.amount_usd.sum()))))
    assets = Path(__file__).resolve().parents[1] / 'dashboard'
    # evaluate_baseline uses the original fixed windows, so compare under the same ones.
    report = export_verified_batch(curated, verification, tmp_path / 'export', assets, splits=LEGACY_SPLITS)
    expected, thresholds, metrics = evaluate_baseline(frame)
    actual = pd.read_parquet(tmp_path / 'export/bq1_scored.parquet').sort_values('source_row_index').reset_index(drop=True)
    expected = expected.sort_values('source_row_index').reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected[actual.columns], check_dtype=False)
    assert report['training_p99'] == thresholds
    for period in ['validation', 'test']:
        assert report['metrics'][period] == pytest.approx(metrics[period])
    snapshot = json.loads((tmp_path / 'export/dashboard/dashboard.json').read_text())
    assert snapshot['aml_scope'] == 'full'
    assert snapshot['full_rows'] == len(frame)
    assert all('is_laundering' not in row for view in snapshot['aml'].values() for row in view['queue'])
    assert snapshot['treasury']['all']['transactions'] == len(frame)
    with pytest.raises(ValueError, match='fresh'):
        export_verified_batch(curated, verification, tmp_path / 'export', assets, splits=LEGACY_SPLITS)


def test_export_derives_row_share_splits_from_the_data_by_default(tmp_path):
    frame = fixture()
    curated = tmp_path / 'input.parquet'
    frame.to_parquet(curated, index=False)
    verification = tmp_path / 'verified.json'
    verification.write_text(json.dumps(dict(status='passed', rows=len(frame), positives=int(frame.is_laundering.sum()), amount_usd=float(frame.amount_usd.sum()))))
    export_verified_batch(curated, verification, tmp_path / 'export', Path(__file__).resolve().parents[1] / 'dashboard')
    snapshot = json.loads((tmp_path / 'export/dashboard/dashboard.json').read_text())
    assert snapshot['splits'] == {'train': ['2022-09-01', '2022-09-09'], 'validation': ['2022-09-09', '2022-09-12'],
                                  'test': ['2022-09-12', '2022-09-13']}
    assert set(snapshot['aml']) == {'validation', 'test'}
