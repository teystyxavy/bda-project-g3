"""A partial failed run must never be promoted by the parity checker."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[1] / 'scripts/verify-cloud-scoring-smoke.py'
    spec = importlib.util.spec_from_file_location('cloud_scoring_verifier', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('state,forensic,job', [
    ('FAILED', False, 'is459-g3-score-frozen'),
    ('RUNNING', True, 'is459-g3-score-frozen'),
    ('STOPPED', True, 'is459-g3-score-frozen'),
    ('SUCCEEDED', True, 'different-job'),
])
def test_reject_uncommitted_or_wrong_job_before_reading_files(tmp_path, verifier, state, forensic, job):
    report = tmp_path / 'run.json'
    report.write_text(json.dumps({'JobRun': {'JobRunState': state, 'JobName': job}}))
    with pytest.raises(ValueError, match='Require a successful scoring job report'):
        verifier.verify(tmp_path / 'missing-output', tmp_path / 'missing-package', report, forensic)


def test_forensic_parity_cannot_mark_failed_pipeline_passed(tmp_path, verifier, monkeypatch):
    import pandas as pd
    expected = pd.DataFrame({
        'transaction_id': [str(i) for i in range(1024)],
        'source_row_index': range(1024), 'timestamp': pd.Timestamp('2022-09-01'),
        'model_score': 0.5,
    })
    queue = pd.DataFrame({'transaction_id': expected.transaction_id.head(100),
                          'model_evidence': 'checked', 'explanation_total_margin': 0.0})
    actual = expected.assign(txn_date='2022-09-01')
    (tmp_path / 'package.json').write_text(json.dumps({'cohort_rows': 1024, 'manifest_sha256': 'a'*64}))
    report = tmp_path / 'run.json'
    report.write_text(json.dumps({'JobRun': {'JobRunState': 'FAILED',
                                            'JobName': 'is459-g3-score-frozen', 'Id': 'failed-run'}}))
    monkeypatch.setattr(verifier.pd, 'read_parquet',
                        lambda path: queue if Path(path).name == 'expected-queue.parquet' else expected)
    monkeypatch.setattr(verifier.ds, 'dataset', lambda *a, **k: SimpleNamespace(
        to_table=lambda: SimpleNamespace(to_pandas=lambda: actual)))
    monkeypatch.setattr(verifier, 'worker_ranker', lambda *a: SimpleNamespace(queue=lambda *a: queue))
    monkeypatch.setattr(verifier, 'digest', lambda path: 'b'*64)
    result = verifier.verify(tmp_path, tmp_path, report, forensic=True)
    assert result['status'] == 'forensic-parity-only-failed-run'
    assert result['pipeline_passed'] is False
    assert result['job_state'] == 'FAILED'
