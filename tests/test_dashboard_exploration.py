import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds

from bda_pipeline.dashboard_exploration import scorer_projection, account_histories, treasury_exploration


def test_capacity_metrics_use_complete_population_and_ties():
    frame = pd.DataFrame({'timestamp': pd.to_datetime(['2022-09-09'] * 300),
        'source_row_index': np.arange(300), 'transaction_id': [str(i) for i in range(300)],
        'is_laundering': [1] * 10 + [0] * 290})
    result = scorer_projection(frame, np.ones(300) * .5, 250)
    assert result['queue'] == [str(i) for i in range(250)]
    assert result['metrics_by_k']['25']['precision_at_k'] == .4
    assert result['metrics_by_k']['100']['recall_at_k'] == 1
    assert result['metrics_by_k']['250']['rows'] == 300
    assert result['metrics_by_k']['100']['average_precision'] == 10 / 300
    assert sum(row['transactions'] for row in result['histogram']) == 300
    assert sum(row['reviewed'] for row in result['daily_by_k']['50']) == 50
    assert all(set(point) == {'recall', 'precision'} for point in result['pr_curve'])


def test_empty_and_no_positive_measurements_remain_unavailable():
    frame = pd.DataFrame({'timestamp': pd.to_datetime([]), 'source_row_index': [],
        'transaction_id': [], 'is_laundering': []})
    result = scorer_projection(frame, np.array([]), 100, model=False)
    assert result['metrics_by_k']['100']['precision_at_k'] is None
    assert result['pr_curve'] == []
    assert result['histogram'][0]['label'] == '0.00–10.00'


def test_history_uses_composite_identity_and_never_future_rows():
    frame = pd.DataFrame({'transaction_id': ['a', 'b', 'c', 'd'],
        'timestamp': pd.to_datetime(['2022-09-08', '2022-09-09', '2022-09-10', '2022-09-08']),
        'source_row_index': [0, 1, 2, 3], 'from_bank': ['01', '01', '01', '02'],
        'from_account': ['0001'] * 4, 'to_bank': ['03'] * 4, 'to_account': ['0002'] * 4,
        'payment_format': ['ACH'] * 4, 'amount_usd_asof': [1.] * 4})
    targets = {'b': {'timestamp': '2022-09-09', 'source_row_index': 1, 'from_bank': '01', 'from_account': '0001'}}
    result = account_histories(ds.dataset(pa.Table.from_pandas(frame)), targets)
    assert result['b']['observed_prior_transactions'] == 1
    assert [event['transaction_id'] for event in result['b']['events']] == ['a']
    assert 'is_laundering' not in result['b']['events'][0]


def test_treasury_heatmap_reconciles_full_volume_and_complete_daily_history():
    rows = []
    for i in range(20):
        rows.append({'txn_date': '2022-09-01', 'from_bank': f'{i:02}', 'to_bank': '99',
            'payment_currency': 'USD', 'receiving_currency': 'EUR', 'volume_usd': float(i+1), 'transaction_count': i+1})
    rows.append({**rows[-1], 'txn_date': '2022-09-02', 'volume_usd': 100., 'transaction_count': 10})
    result = treasury_exploration(pd.DataFrame(rows))
    assert sum(row['volume_usd'] for row in result['heatmaps']['all']) == 310
    assert sum(row['transaction_count'] for row in result['heatmaps']['all']) == 220
    assert 'Other banks' in result['heatmap_nodes']
    assert result['daily_shares'][1]['top1_share'] == 1
    assert result['comparisons']['2022-09-01'] is None
    assert result['comparisons']['2022-09-02']['previous_volume'] == 210
    corridor = next(row for row in result['corridors'] if row['from_bank'] == '00')
    assert corridor['daily'][1]['volume_usd'] == 0
    assert corridor['daily'][0]['share'] == 1 / 210


def test_one_day_sample_has_no_invented_comparison():
    frame = pd.DataFrame([{'txn_date': '2022-09-01', 'from_bank': '01', 'to_bank': '02',
        'payment_currency': 'USD', 'receiving_currency': 'USD', 'volume_usd': 1., 'transaction_count': 1}])
    result = treasury_exploration(frame)
    assert result['comparisons']['all'] is None
    assert result['comparisons']['2022-09-01'] is None


def test_development_date_breakdown_uses_only_test_dates_and_keeps_groups_disjoint():
    from bda_pipeline.dashboard_exploration import evaluation_date_breakdown
    from bda_pipeline.model import INPUT_COLUMNS
    # Baseline fixture supplies every model input; features are irrelevant to this metric-only split.
    from test_batch import fixture
    frame=fixture()
    from bda_pipeline.analytics import score_baseline
    frame=score_baseline(frame,{'ACH':50.,'Wire':50.})
    result=evaluation_date_breakdown(ds.dataset(pa.Table.from_pandas(frame)),np.linspace(0,1,len(frame)),
        ['2022-09-09'],'2022-09-09','2022-09-13','HI-Small')
    assert result['previous_test_dates']['metrics']['rows']==60
    assert result['unseen_dates']['metrics']['rows']==60
    assert 'not new holdout evidence' in result['previous_test_dates']['definition']
    assert 'without retuning' in result['unseen_dates']['definition']
