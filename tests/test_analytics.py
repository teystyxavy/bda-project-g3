import pandas as pd
import pytest
from bda_pipeline.analytics import add_behaviour, score_baseline, ranking_metrics, concentration, corridor_totals


def test_windows_exclude_peers_and_future_include_boundary_and_separate_banks():
    d = pd.DataFrame(dict(source_row_index=range(6),
        timestamp=pd.to_datetime(['2022-09-01 10:00','2022-09-01 10:00','2022-09-01 11:00',
                                  '2022-09-01 11:01','2022-09-01 11:02','2022-09-03 12:00']),
        from_bank=['001','1','1','1','2','1'], from_account=['A']*6,
        to_bank=['2','3','2','2','2','2'], to_account=['X']*6))
    f = add_behaviour(d)
    assert f.txn_count_1h.tolist() == [0,0,2,1,0,0]
    assert f.distinct_counterparties_24h.tolist() == [0,0,2,2,0,0]
    assert f.txn_count_7d.tolist() == [0,0,2,3,0,4]
    pd.testing.assert_frame_equal(add_behaviour(d.iloc[:4]), f.iloc[:4])


def test_ranking_labels_do_not_influence_scores_and_metrics_handle_ties():
    d = pd.DataFrame(dict(payment_format=['ACH','ACH','Wire'], amount_usd_asof=[101,50,1000],
        txn_count_1h=[0,11,0], distinct_counterparties_24h=[0,0,0],
        timestamp=pd.to_datetime(['2022-09-16']*3), source_row_index=[0,1,2], is_laundering=[1,0,0]))
    r = score_baseline(d, {'ACH':100})
    assert r.risk_score.tolist() == [80,70,0]
    flipped = score_baseline(d.assign(is_laundering=1-d.is_laundering), {'ACH':100})
    assert flipped.risk_score.tolist() == r.risk_score.tolist()
    m = ranking_metrics(r, 2)
    assert m['precision_at_k'] == .5
    assert m['recall_at_k'] == 1
    assert m['average_precision'] == .5
    assert ranking_metrics(r.assign(is_laundering=0))['recall_at_k'] is None


def test_corridors_reconcile_and_hhi_combines_currencies():
    d = pd.DataFrame(dict(txn_date=['2022-09-01']*3, from_bank=['01','1','2'],
        to_bank=['2','02','1'], payment_currency=['Euro','US Dollar','Euro'],
        receiving_currency=['Euro','US Dollar','Euro'], amount_usd=[25.,25.,50.]))
    daily = corridor_totals(d)
    assert daily.transaction_count.sum() == 3
    assert daily.volume_usd.sum() == 100
    c = concentration(daily)
    assert c.hhi.iloc[0] == pytest.approx(.5)
    assert c.top5_share.iloc[0] == 1


def test_average_precision_groups_tied_scores():
    r = pd.DataFrame({'risk_score': [50, 50], 'is_laundering': [1, 0]})
    assert ranking_metrics(r)['average_precision'] == .5


def test_full_runner_fixture_smoke(tmp_path, monkeypatch):
    import importlib.util
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('analysis_runner', root / 'scripts/run-local-analysis.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    dataset = tmp_path / 'dataset'
    dataset.mkdir()
    pd.DataFrame({
        'Timestamp': ['2022/09/01 00:00','2022/09/07 10:00','2022/09/09 10:00'],
        'From Bank': ['001']*3, 'Account': ['A']*3, 'To Bank': ['002']*3,
        'Other Account': ['B']*3, 'Amount Received': [10, 20, 30],
        'Receiving Currency': ['Euro']*3, 'Amount Paid': [10, 20, 30],
        'Payment Currency': ['Euro']*3, 'Payment Format': ['ACH']*3,
        'Is Laundering': [0, 1, 1],
    }).to_csv(dataset / 'HI-Small_Trans.csv', index=False)
    pd.DataFrame({'Bank Name':['A','B'], 'Bank ID':['1','2'], 'Account Number':['A','B'],
                  'Entity ID':['E1','E2'], 'Entity Name':['Alice','Bob']}).to_csv(dataset / 'HI-Small_accounts.csv', index=False)
    fx = tmp_path / 'fx.csv'
    pd.DataFrame({'txn_date':pd.date_range('2022-08-31','2022-09-18'),
                  'currency':'Euro', 'usd_rate':2.0}).to_csv(fx, index=False)
    output = tmp_path / 'result'
    monkeypatch.setattr('sys.argv', ['runner', '--dataset', str(dataset), '--fx-rates', str(fx),
                                    '--output', str(output), '--sender-modulus', '1'])
    runner.main()
    report = json.loads((output / 'run_report.json').read_text())
    assert report['rows'] == 3
    assert report['missing_from_metadata'] == 0
    assert report['metrics']['test']['recall_at_k'] == 1
    assert pd.read_parquet(output / 'bq2_daily_corridors.parquet').volume_usd.sum() == 120
    assert 'is_laundering' not in pd.read_csv(output / 'review_queue.csv').columns
    with pytest.raises(SystemExit):
        runner.main()
