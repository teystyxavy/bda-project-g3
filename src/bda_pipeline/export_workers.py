"""Bounded parallel score reconciliation; workers never mutate global row trackers."""
from concurrent.futures import ProcessPoolExecutor
from collections import deque
import multiprocessing
import os

from .cloud_scores import feature_fingerprints
from .model import SavedRanker

_RANKER = None


def _initialize(model_dir):
    global _RANKER
    _RANKER = SavedRanker(model_dir, threads=1)


def _compute(frame, predict):
    import numpy as np
    for column, vocabulary in _RANKER.vocabulary.items():
        if not frame[column].isin(vocabulary).all():
            raise ValueError('Frozen model categorical compatibility failure: ' + column)
    fingerprints = feature_fingerprints(frame)
    difference = 0.0
    if predict:
        actual = frame.model_score.to_numpy(dtype='float64')
        if not np.isfinite(actual).all() or ((actual < 0) | (actual > 1)).any():
            raise ValueError('Invalid model ranking score')
        expected = _RANKER.predict(frame)
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-7)
        difference = float(np.abs(actual - expected).max()) if len(frame) else 0.0
    return fingerprints, difference


class ScoreWorkers:
    def __init__(self, model_dir, workers=None):
        self.model_dir = str(model_dir)
        self.workers = workers or max(1, min(8, (os.cpu_count() or 1) // 2))
        self.pool = None

    def __enter__(self):
        self.pool = ProcessPoolExecutor(max_workers=self.workers,
            mp_context=multiprocessing.get_context('spawn'),
            initializer=_initialize, initargs=(self.model_dir,))
        return self

    def batches(self, batches, predict=False):
        pending = deque()
        try:
            for batch in batches:
                if not batch.num_rows:
                    continue
                frame = batch.to_pandas()
                pending.append((frame, self.pool.submit(_compute, frame, predict)))
                if len(pending) >= self.workers:
                    frame, future = pending.popleft()
                    yield frame, future.result()
            while pending:
                frame, future = pending.popleft()
                yield frame, future.result()
        finally:
            for _, future in pending:
                future.cancel()

    def __exit__(self, *exc):
        self.pool.shutdown(wait=True, cancel_futures=True)
