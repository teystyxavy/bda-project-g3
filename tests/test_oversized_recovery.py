import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_ingestion import MemoryS3

spec = importlib.util.spec_from_file_location('oversized_recovery',
    Path(__file__).resolve().parents[1] / 'scripts/ingestion/recover-oversized-export.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def setup_ready():
    s3 = MemoryS3()
    args = {'--RUN_ID': 'test-run', '--OUTPUT': f's3://{recovery.BUCKET}/tmp/ingest/recovery-exports/test-run/review/'}
    plan = dict(job='test-job', wait_run='original', arguments=args, update={}, network_code_sha256='code')
    run = dict(JobRunState='FAILED', Arguments=args, ErrorMessage='ValueError: Implausible network: one ring has 26370 accounts')
    raw = b'{"run_id":"test-run"}'
    s3.data[recovery.BUCKET, 'pipeline/runs/test-run/raw/manifest.json'] = raw
    key = 'tmp/ingest/export-checkpoints/test-run/identity/result/manifest.json'
    s3.data[recovery.BUCKET, key] = json.dumps({'identity': {
        'network_code_sha256': 'code', 'manifest_sha256': hashlib.sha256(raw).hexdigest()}}).encode()
    glue = SimpleNamespace(get_paginator=lambda name: SimpleNamespace(paginate=lambda **kw: [{'JobRuns': [run]}]))
    return plan, run, glue, s3, key


def test_ready_requires_exact_failure_complete_checkpoint_and_empty_output():
    plan, run, glue, s3, key = setup_ready()
    assert recovery.ready(glue, s3, plan, run) == key
    run['ErrorMessage'] = 'Unrelated error'
    with pytest.raises(ValueError, match='specific oversized'):
        recovery.ready(glue, s3, plan, run)
    run['ErrorMessage'] = 'Implausible network: one ring has 1000 accounts'
    s3.data[recovery.BUCKET, 'tmp/ingest/recovery-exports/test-run/review/partial'] = b'partial'
    with pytest.raises(ValueError, match='not empty'):
        recovery.ready(glue, s3, plan, run)
    del s3.data[recovery.BUCKET, 'tmp/ingest/recovery-exports/test-run/review/partial']
    del s3.data[recovery.BUCKET, key]
    with pytest.raises(ValueError, match='complete matching'):
        recovery.ready(glue, s3, plan, run)


def test_active_worker_prevents_launch():
    plan, run, glue, s3, _ = setup_ready()
    glue.get_paginator = lambda name: SimpleNamespace(paginate=lambda **kw: [{'JobRuns': [{'JobRunState': 'RUNNING'}]}])
    with pytest.raises(ValueError, match='still active'):
        recovery.ready(glue, s3, plan, run)


@pytest.mark.parametrize('status,count', [('SUCCEEDED', 0), ('FAILED', 1)])
def test_watcher_launches_at_most_once(tmp_path, status, count):
    plan, run, glue, s3, _ = setup_ready()
    run['JobRunState'] = status
    launches = []
    glue.get_job_run = lambda **kw: {'JobRun': run}
    glue.update_job = lambda **kw: None
    glue.start_job_run = lambda **kw: launches.append(kw) or {'JobRunId': 'corrected'}
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    recovery.watch(glue, s3, tmp_path)
    assert len(launches) == count
    with pytest.raises(FileExistsError):
        recovery.watch(glue, s3, tmp_path)
    assert len(launches) == count


def test_start_now_isolated_output_read_only_checkpoints_and_one_launch(tmp_path):
    plan, run, glue, s3, _ = setup_ready()
    run['JobRunState'] = 'RUNNING'
    plan['update'] = {'NonOverridableArguments': {'--NETWORK_CHECKPOINTS': 'true'}}
    launches, updates = [], []
    glue.update_job = lambda **kw: updates.append(kw)
    glue.start_job_run = lambda **kw: launches.append(kw) or {'JobRunId': 'concurrent'}
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    recovery.start_now(glue, s3, tmp_path)
    assert launches[0]['Arguments']['--OUTPUT'] == plan['arguments']['--OUTPUT'] + 'backup-now-20261108/'
    assert updates[0]['JobUpdate']['NonOverridableArguments']['--CHECKPOINT_READ_ONLY'] == 'true'
    assert updates[0]['JobUpdate']['ExecutionProperty']['MaxConcurrentRuns'] == 2
    with pytest.raises(FileExistsError):
        recovery.start_now(glue, s3, tmp_path)
    assert len(launches) == 1


def test_hardened_handover_waits_for_checkpoint_then_stops_only_consumer(tmp_path, monkeypatch):
    plan, source, glue, s3, _ = setup_ready()
    plan.update(replace_run='corrected', update={'Timeout': 720})
    plan['arguments'] = dict(plan['arguments'], **{'--OUTPUT': plan['arguments']['--OUTPUT'] + 'hardened/'})
    current = {'JobRunState': 'RUNNING'}
    source['JobRunState'] = 'RUNNING'
    checks = iter([None, 'matching/result/manifest.json'])
    monkeypatch.setattr(recovery, 'complete_source_checkpoint', lambda *args: next(checks))
    monkeypatch.setattr(recovery.time, 'sleep', lambda seconds: None)
    stops, starts, updates = [], [], []
    glue.get_job_run = lambda **kw: {'JobRun': current if kw['RunId'] == 'corrected' else source}
    def stop(**kw):
        stops.append(kw)
        current['JobRunState'] = 'STOPPED'
        return {'SuccessfulSubmissions': [{'JobRunId': 'corrected'}]}
    glue.batch_stop_job_run = stop
    glue.update_job = lambda **kw: updates.append(kw)
    glue.start_job_run = lambda **kw: starts.append(kw) or {'JobRunId': 'hardened'}
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    recovery.watch_hardened(glue, s3, tmp_path)
    assert stops == [{'JobName': 'test-job', 'JobRunIds': ['corrected']}]
    assert source['JobRunState'] == 'RUNNING'
    assert len(starts) == 1 and starts[0]['Timeout'] == 720
    assert json.loads((tmp_path / 'status.json').read_text())['status'] == 'hardened_export_started'
    with pytest.raises(FileExistsError):
        recovery.watch_hardened(glue, s3, tmp_path)


def test_hardened_handover_never_stops_without_completed_source_checkpoint(tmp_path, monkeypatch):
    plan, source, glue, s3, _ = setup_ready()
    plan['replace_run'] = 'corrected'
    monkeypatch.setattr(recovery, 'complete_source_checkpoint', lambda *args: None)
    glue.get_job_run = lambda **kw: {'JobRun': {'JobRunState': 'RUNNING'} if kw['RunId'] == 'corrected' else source}
    glue.batch_stop_job_run = lambda **kw: pytest.fail('Must not stop a worker without a checkpoint')
    glue.start_job_run = lambda **kw: pytest.fail('Must not launch without checkpoint')
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='without a complete'):
        recovery.watch_hardened(glue, s3, tmp_path)


def test_complete_source_checkpoint_requires_all_committed_table_files():
    plan, _, _, s3, key = setup_ready()
    manifest = json.loads(s3.data[recovery.BUCKET, key])
    names = {'totals', 'candidates', 'pairs', 'loops', 'ring_members', 'ring_edges', 'accounts', 'ring_payments'}
    manifest['files'] = {name: 'a' * 64 for name in names}
    s3.data[recovery.BUCKET, key] = json.dumps(manifest).encode()
    for name in names:
        s3.data[recovery.BUCKET, key.removesuffix('manifest.json') + name + '.parquet'] = b'parquet'
    assert recovery.complete_source_checkpoint(s3, plan) == key
    s3.data[recovery.BUCKET, key.removesuffix('manifest.json') + 'loops.parquet'] = b''
    with pytest.raises(ValueError, match='Incomplete source result'):
        recovery.complete_source_checkpoint(s3, plan)


@pytest.mark.parametrize('message,near', [
    ('EXPORT_NETWORK phase=payment_pass rows=74 total=100', False),
    ('EXPORT_NETWORK phase=payment_pass rows=75 total=100', True),
    ('EXPORT_PHASE network_recomputed', True),
    ('EXPORT_PHASE model_view test', True),
    ('EXPORT_CHECKPOINT restored=links', False),
])
def test_handover_avoids_discarding_nearly_completed_network_work(message, near):
    logs = SimpleNamespace(get_log_events=lambda **kw: {'events': [{'message': message}]})
    assert recovery.corrected_is_near_network_completion(logs, 'corrected') == near


def test_handover_keeps_advanced_corrected_run_until_success(tmp_path, monkeypatch):
    plan, _, glue, s3, _ = setup_ready()
    plan['replace_run'] = 'corrected'
    current = {'JobRunState': 'RUNNING'}
    monkeypatch.setattr(recovery, 'complete_source_checkpoint', lambda *args: 'complete/result/manifest.json')
    monkeypatch.setattr(recovery.time, 'sleep', lambda seconds: current.update(JobRunState='SUCCEEDED'))
    glue.get_job_run = lambda **kw: {'JobRun': current}
    glue.batch_stop_job_run = lambda **kw: pytest.fail('Advanced corrected run must not be stopped')
    glue.start_job_run = lambda **kw: pytest.fail('Must not relaunch a successful corrected run')
    logs = SimpleNamespace(get_log_events=lambda **kw: {'events': [{'message': 'EXPORT_PHASE model_view test'}]})
    (tmp_path / 'plan.json').write_text(json.dumps(plan))
    recovery.watch_hardened(glue, s3, tmp_path, logs)
    assert json.loads((tmp_path / 'status.json').read_text())['status'] == 'corrected_succeeded_no_replacement'
