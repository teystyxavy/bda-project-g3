import numpy as np
import pandas as pd
import pytest

from bda_pipeline import ingest_compute
from bda_pipeline.analytics import add_behaviour
from bda_pipeline.batch import FEATURES, RECEIVER_FEATURES, SENDER_FEATURES
from bda_pipeline.ingest_compute import verify_behaviour


def history(rows=600, seed=3):
    """Chronological transactions among 14 accounts with every behaviour feature computed over the complete history."""
    rng = np.random.default_rng(seed)
    accounts = [f'A{i}' for i in range(14)]
    frame = pd.DataFrame(dict(
        source_row_index=np.arange(rows),
        timestamp=pd.Timestamp('2022-09-01') + pd.to_timedelta(np.sort(rng.integers(0, 9 * 86400, rows)), unit='s'),
        from_bank=rng.choice(['001', '02'], rows), to_bank=rng.choice(['001', '02'], rows),
        from_account=rng.choice(accounts, rows), to_account=rng.choice(accounts, rows),
        amount_usd_asof=rng.integers(1, 5000, rows).astype(float)))
    return add_behaviour(frame)


def cohorts(full):
    """The complete history of four senders and of four receivers, as the 1% hash cohorts provide."""
    return [full[full.from_account.isin(['A0', 'A1', 'A2', 'A3'])]], [full[full.to_account.isin(['A5', 'A6', 'A7', 'A8'])]]


def test_each_feature_is_proved_by_exactly_one_cohort():
    assert sorted(SENDER_FEATURES + RECEIVER_FEATURES) == sorted(FEATURES)
    assert not set(SENDER_FEATURES) & set(RECEIVER_FEATURES)
    assert {'amount_sum_24h', 'amount_sum_7d', 'corridor_prior_count'} <= set(SENDER_FEATURES)
    assert {'in_amount_sum_24h', 'in_amount_sum_7d', 'distinct_senders_7d'} <= set(RECEIVER_FEATURES)


def test_complete_cohort_histories_verify_all_features():
    senders, receivers = cohorts(history())
    assert verify_behaviour(senders, receivers) == len(senders[0]) + len(receivers[0])
    assert verify_behaviour([], []) == 0


def test_the_old_five_and_five_split_fails_on_the_new_features(monkeypatch):
    # Regression: FEATURES[5:] put the outgoing amount sums on the receiver cohort, whose rows are not the sender's full history.
    monkeypatch.setattr(ingest_compute, 'SENDER_FEATURES', FEATURES[:5])
    monkeypatch.setattr(ingest_compute, 'RECEIVER_FEATURES', FEATURES[5:])
    with pytest.raises(AssertionError):
        verify_behaviour(*cohorts(history()))


@pytest.mark.parametrize('side, column', [(0, 'amount_sum_7d'), (0, 'corridor_prior_count'), (1, 'in_amount_sum_24h'), (1, 'distinct_senders_7d')])
def test_a_tampered_value_is_caught_on_either_side(side, column):
    pair = list(cohorts(history()))
    tampered = pair[side][0].copy()
    tampered.loc[tampered.index[len(tampered) // 2], column] += 1
    pair[side] = [tampered]
    with pytest.raises(AssertionError):
        verify_behaviour(*pair)
