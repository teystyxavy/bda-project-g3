import pandas as pd
import pytest
from bda_pipeline.dashboard import aml_view, treasury_view, diagnose_rules


def test_treasury_uses_full_denominator_before_top15():
    frame=pd.DataFrame({'from_bank':[str(index) for index in range(20)],'to_bank':'99',
                        'payment_currency':'Euro','receiving_currency':'US Dollar',
                        'volume_usd':10.,'transaction_count':2})
    view=treasury_view(frame)
    assert view['transactions']==40
    assert view['hhi']==pytest.approx(.05)
    assert view['top5_share']==pytest.approx(.25)
    assert len(view['banks'])==15
    assert sum(row['share'] for row in view['banks'])==pytest.approx(.75)


def test_aml_queue_omits_labels_and_validation_diagnostics_ignore_test():
    frame=pd.DataFrame({'transaction_id':['a','b','c'], 'timestamp':pd.to_datetime(['2022-09-01','2022-09-07','2022-09-09']),
        'from_bank':'01','from_account':'A','to_bank':'02','to_account':'B','payment_format':'ACH',
        'payment_currency':'Euro','amount_usd_asof':[10,11,99999], 'risk_score':[50,70,70],
        'risk_reasons':['ACH','ACH; above training P99','ACH; above training P99'],
        'txn_count_1h':0,'distinct_counterparties_24h':0,'source_row_index':[0,1,2],
        'is_laundering':[0,1,0], 'flagged':[False,True,True]})
    view=aml_view(frame)
    assert len(view['queue'])==3
    assert 'is_laundering' not in view['queue'][0]
    assert sum(row['transactions'] for row in view['histogram'])==3
    first=diagnose_rules(frame)
    frame.loc[2,['amount_usd_asof','is_laundering']]=[1,1]
    assert diagnose_rules(frame)==first
    assert first['variants'][0]['rows']==1


def test_empty_aml_period_has_unavailable_metrics():
    columns=['risk_score','timestamp','source_row_index','is_laundering','flagged','transaction_id',
             'from_bank','from_account','to_bank','to_account','payment_format','payment_currency',
             'amount_usd_asof','risk_reasons','txn_count_1h','distinct_counterparties_24h']
    frame=pd.DataFrame(columns=columns)
    frame['timestamp']=pd.to_datetime(frame.timestamp)
    view=aml_view(frame)
    assert view['metrics']['precision_at_k'] is None
    assert view['queue']==[]
