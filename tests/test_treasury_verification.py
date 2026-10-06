import pandas as pd
import pytest

from bda_pipeline.treasury_verification import compare_corridors, expected_corridors, same_query


def test_athena_statement_delimiter_normalization_is_narrow():
    assert same_query('SELECT amount_usd FROM full_transactions', 'SELECT amount_usd FROM full_transactions;\n')
    assert not same_query('SELECT amount_usd_asof FROM full_transactions', 'SELECT amount_usd FROM full_transactions;')
    assert not same_query('SELECT amount_usd FROM full_transactions; DELETE FROM full_transactions', 'SELECT amount_usd FROM full_transactions;')


def reference():
    return pd.DataFrame([
        ['2022-09-01', '001', '2', 'Euro', 'US Dollar', 75.0],
        ['2022-09-01', '1', '2', 'US Dollar', 'Euro', 25.0],
        ['2022-09-02', '2', '001', 'Euro', 'Euro', 0.0],
    ], columns=['txn_date','from_bank','to_bank','payment_currency','receiving_currency','amount_usd'])


def test_batched_reconciliation_preserves_keys_and_zero_day():
    source = reference()
    expected = expected_corridors([source.iloc[:1], source.iloc[1:]], 3)
    assert len(expected) == 6
    assert set(expected.loc[expected.corridor_type.eq('bank'),'from_node']) == {'001','1','2'}
    assert expected.loc[expected.txn_date.eq('2022-09-02'),'daily_hhi'].isna().all()
    assert compare_corridors(expected, expected.sample(frac=1), 3)['status'] == 'passed'


@pytest.mark.parametrize('metric', ['transactions','volume_usd','volume_share','daily_hhi'])
def test_changed_treasury_metric_rejected(metric):
    expected = expected_corridors([reference()],3)
    changed = expected.copy()
    changed.loc[0,metric] += 1
    with pytest.raises(ValueError, match='metric mismatch'):
        compare_corridors(expected, changed, 3)


def test_missing_duplicate_and_normalized_keys_rejected():
    expected = expected_corridors([reference()],3)
    for changed in [expected.iloc[1:], pd.concat([expected,expected.iloc[:1]])]:
        with pytest.raises(ValueError):
            compare_corridors(expected, changed, 3)
    normalized=expected.copy()
    normalized['from_node']=normalized.from_node.replace('001','1')
    with pytest.raises(ValueError):
        compare_corridors(expected,normalized,3)


def test_wrong_reference_population_rejected():
    with pytest.raises(ValueError,match='population mismatch'):
        expected_corridors([reference()],4)
