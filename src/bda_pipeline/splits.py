"""Chronological train/validation/test windows shared by training, the rule baseline and the dashboard.
Windows are half-open ``(start, end)`` ISO dates; ``LEGACY_SPLITS`` is the original four-window HI-Small layout."""
import json
from datetime import date, timedelta
from itertools import accumulate
from pathlib import Path

# Share of rows in train, validation and test; boundaries snap to whole days.
SPLIT_RATIOS = (0.6, 0.2, 0.2)
# Layout of models and snapshots saved before splits were recorded.
LEGACY_SPLITS = {'train': ('2022-09-01', '2022-09-07'), 'validation': ('2022-09-07', '2022-09-09'),
                 'test': ('2022-09-09', '2022-09-11'), 'tail': ('2022-09-11', '2022-09-19')}


def row_share_splits(daily_counts, ratios=SPLIT_RATIOS):
    """Splits ``{iso_date: rows}`` chronologically so each window holds about its share of rows, with boundaries at the day whose cumulative share is nearest.
    Raises ValueError for fewer than 3 days; every window gets at least one day."""
    days = sorted(daily_counts)
    if len(days) < 3:
        raise ValueError('Need at least 3 days of data to split into train, validation and test')
    total = sum(daily_counts.values())
    cumulative = list(accumulate(daily_counts[day] for day in days))

    def cut(share, low, high):
        # Number of days before the boundary; ties go to the earlier boundary.
        return min(range(low, high + 1), key=lambda k: (abs(cumulative[k - 1] - share * total), k))

    train = cut(ratios[0], 1, len(days) - 2)
    validation = cut(ratios[0] + ratios[1], train + 1, len(days) - 1)
    end = date.fromisoformat(days[-1]) + timedelta(1)
    return {'train': (days[0], days[train]), 'validation': (days[train], days[validation]),
            'test': (days[validation], end.isoformat())}


def eval_names(splits):
    """Names of the evaluation windows (everything except ``train``), in order."""
    return [name for name in splits if name != 'train']


def run_splits(manifest, model_dir=None, daily_counts=None):
    """Windows for a run: derived from its daily row counts when training, otherwise the frozen model's recorded splits.
    Models saved before splits were recorded fall back to ``LEGACY_SPLITS``."""
    if manifest.get('train'):
        if daily_counts is None:
            raise ValueError("Training needs the run's daily row counts")
        return row_share_splits(daily_counts)
    recorded = None
    report = Path(model_dir or '.') / 'comparison.json'
    if model_dir is not None and report.exists():
        recorded = json.loads(report.read_text(encoding='utf-8')).get('splits')
    return {name: tuple(window) for name, window in (recorded or LEGACY_SPLITS).items()}


def window_label(window):
    """Human-readable inclusive range for a half-open window, e.g. ``2022-09-07 to 2022-09-08``."""
    last = date.fromisoformat(window[1]) - timedelta(1)
    return f'{window[0]} to {last.isoformat()}'
