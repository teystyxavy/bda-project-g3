import datetime
import json

import numpy as np
import pytest

from bda_pipeline.splits import LEGACY_SPLITS, eval_names, row_share_splits, run_splits, window_label

# Rows per day of HI-Small, 1-18 September 2022: dense for ten days, then a trickle.
HI_SMALL = [1114921, 754449, 207382, 207430, 482650, 482089, 482751, 482773, 654467, 208325,
            396, 281, 184, 121, 46, 46, 23, 11]
COUNTS = {f'2022-09-{day:02d}': rows for day, rows in enumerate(HI_SMALL, 1)}


def test_hi_small_keeps_the_train_and_validation_windows_and_merges_the_tail_into_test():
    assert row_share_splits(COUNTS) == {
        'train': ('2022-09-01', '2022-09-07'), 'validation': ('2022-09-07', '2022-09-09'),
        'test': ('2022-09-09', '2022-09-19')}


def test_boundaries_follow_row_share_not_calendar_days():
    # 30 days with almost everything in the first ten: a calendar split would put validation in the empty days.
    counts = {(datetime.date(2022, 9, 1) + datetime.timedelta(i)).isoformat(): 1000 if i < 10 else 1 for i in range(30)}
    splits = row_share_splits(counts)
    assert splits['train'] == ('2022-09-01', '2022-09-07')
    assert splits['validation'] == ('2022-09-07', '2022-09-09')
    assert splits['test'] == ('2022-09-09', '2022-10-01')


@pytest.mark.parametrize('seed', range(20))
def test_windows_tile_the_range_with_at_least_a_day_each(seed):
    rng = np.random.default_rng(seed)
    days = int(rng.integers(3, 80))
    start = datetime.date(2022, 1, 1)
    counts = {(start + datetime.timedelta(i)).isoformat(): int(rng.integers(0, 5000)) for i in range(days)}
    if not sum(counts.values()):
        counts[start.isoformat()] = 1
    windows = list(row_share_splits(counts).values())
    assert windows[0][0] == start.isoformat()
    assert windows[-1][1] == (start + datetime.timedelta(days)).isoformat()
    assert [w[1] for w in windows[:-1]] == [w[0] for w in windows[1:]]
    assert all(w[0] < w[1] for w in windows)


def test_gaps_in_the_calendar_and_too_few_days():
    splits = row_share_splits({'2022-09-01': 10, '2022-09-05': 10, '2022-09-09': 10})
    assert splits == {'train': ('2022-09-01', '2022-09-05'), 'validation': ('2022-09-05', '2022-09-09'),
                      'test': ('2022-09-09', '2022-09-10')}
    with pytest.raises(ValueError, match='at least 3 days'):
        row_share_splits({'2022-09-01': 5, '2022-09-02': 5})


def test_run_splits_derives_when_training_and_reads_the_model_otherwise(tmp_path):
    manifest = {'train': True}
    assert run_splits(manifest, daily_counts=COUNTS)['test'] == ('2022-09-09', '2022-09-19')
    with pytest.raises(ValueError, match='daily row counts'):
        run_splits(manifest)
    # Frozen scoring keeps the model's own windows, whatever the run's data looks like.
    manifest['train'] = False
    assert run_splits(manifest, tmp_path, COUNTS) == LEGACY_SPLITS
    recorded = row_share_splits(COUNTS)
    (tmp_path / 'comparison.json').write_text(json.dumps({'splits': recorded}))
    assert run_splits(manifest, tmp_path) == recorded


def test_eval_names_and_labels():
    assert eval_names(LEGACY_SPLITS) == ['validation', 'test', 'tail']
    assert eval_names(row_share_splits(COUNTS)) == ['validation', 'test']
    assert window_label(('2022-09-07', '2022-09-09')) == '2022-09-07 to 2022-09-08'
