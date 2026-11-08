import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_ingestion import MemoryS3

spec = importlib.util.spec_from_file_location('export_fallback',
    Path(__file__).resolve().parents[1] / 'scripts/ingestion/export-fallback.py')
fallback = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fallback)


def fixture(status):
    plan = {'original_job': 'original', 'original_run': 'jr-original', 'fallback_job': 'fallback',
            'arguments': {'--OUTPUT': f's3://{fallback.BUCKET}/pipeline/runs/test-run/export/'}}
    original = {'JobRunState': status, 'Arguments': plan['arguments']}
    started = []
    glue = SimpleNamespace(get_job_run=lambda **kw: {'JobRun': original},
        get_paginator=lambda name: SimpleNamespace(paginate=lambda **kw: [{'Jobs': []}]),
        start_job_run=lambda **kw: started.append(kw) or {'JobRunId': 'jr-fallback'})
    return plan, original, glue, MemoryS3(), started


def test_fallback_requires_failure_and_empty_destination():
    plan, original, glue, s3, _ = fixture('RUNNING')
    with pytest.raises(ValueError, match='not failed'):
        fallback.safe_to_launch(glue, s3, plan, original)
    original['JobRunState'] = 'TIMEOUT'
    fallback.safe_to_launch(glue, s3, plan, original)
    s3.data[fallback.BUCKET, 'pipeline/runs/test-run/export/partial'] = b'partial'
    with pytest.raises(ValueError, match='refusing to overwrite'):
        fallback.safe_to_launch(glue, s3, plan, original)


@pytest.mark.parametrize('status,launches', [('SUCCEEDED', 0), ('STOPPED', 0), ('FAILED', 1), ('TIMEOUT', 1)])
def test_watcher_starts_at_most_once(tmp_path, status, launches):
    plan, _, glue, s3, started = fixture(status)
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    fallback.watch(glue, s3, tmp_path)
    assert len(started) == launches
    with pytest.raises(FileExistsError):
        fallback.watch(glue, s3, tmp_path)
    assert len(started) == launches


def test_active_export_owner_blocks_fallback():
    plan, original, glue, s3, started = fixture('FAILED')
    def paginator(name):
        rows = [{'Jobs': [{'Name': 'other'}]}] if name == 'get_jobs' else [
            {'JobRuns': [{'JobRunState': 'RUNNING', 'Arguments': plan['arguments']}]}]
        return SimpleNamespace(paginate=lambda **kw: rows)
    glue.get_paginator = paginator
    with pytest.raises(ValueError, match='active worker'):
        fallback.safe_to_launch(glue, s3, plan, original)
    assert not started
