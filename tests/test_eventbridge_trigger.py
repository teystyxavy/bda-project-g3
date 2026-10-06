import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def cli():
    spec = importlib.util.spec_from_file_location('trigger_cli', ROOT / 'scripts/ingestion/ingest.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class S3:
    def __init__(self): self.keys = []
    def list_objects_v2(self, **kwargs): return {}
    def put_object(self, **kwargs): self.keys.append(kwargs['Key'])
    def upload_file(self, path, bucket, key, ExtraArgs): self.keys.append(key)


class States:
    class exceptions:
        class ExecutionDoesNotExist(Exception): pass
    def __init__(self, appears_after):
        self.calls = 0
        self.appears_after = appears_after
    def start_execution(self, **kwargs): raise AssertionError('EventBridge mode must not start the batch itself')
    def describe_execution(self, executionArn):
        self.calls += 1
        if self.calls <= self.appears_after: raise self.exceptions.ExecutionDoesNotExist()
        return {'executionArn': executionArn}


def manifest():
    return {'run_id': 'trigger-run', 'rerun': True, 'processing_sha256': 'a' * 64,
            'files': {name: {'sha256': 'b' * 64} for name in ['transactions.csv', 'accounts.csv', 'fx.csv']}}


def test_upload_writes_trigger_last_and_follows_eventbridge_start(tmp_path, monkeypatch):
    module = cli()
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    s3, states = S3(), States(appears_after=2)
    receipt = module.start(manifest(), tmp_path, s3, states, 'eventbridge')
    assert s3.keys[-2:] == ['pipeline/runs/trigger-run/raw/manifest.json', 'pipeline/runs/trigger-run/raw/trigger.json']
    assert receipt['execution_arn'].endswith(':execution:is459-g3-ingest:trigger-run')
    assert receipt['started_by'] == 'eventbridge' and states.calls == 3


def test_missing_trigger_stack_fails_clearly(tmp_path, monkeypatch):
    module = cli()
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(module.time, 'monotonic', iter([0, 0, 200]).__next__)
    with pytest.raises(RuntimeError, match='EventBridge did not start'):
        module.start(manifest(), tmp_path, S3(), States(appears_after=10), 'eventbridge')


def test_wrapper_validates_key_and_never_repeats_the_batch():
    workflow = json.loads((ROOT / 'infra/eventbridge-trigger/workflow.asl.json').read_text())
    states = workflow['States']
    assert states['StartBatch']['Resource'] == 'arn:aws:states:::states:startExecution'
    assert states['StartBatch']['Parameters']['Name.$'] == '$.trigger.run_id'
    assert 'Retry' not in states['StartBatch']
    assert states['StartBatch']['Catch'][0] == {'ErrorEquals': ['StepFunctions.ExecutionAlreadyExistsException'],
                                                'ResultPath': '$.failure', 'Next': 'AlreadyStarted'}
    assert states['ValidKey']['Default'] == 'Rejected'
    # Mirror the wrapper's key parsing: only pipeline/runs/<run>/raw/trigger.json is accepted.
    for key, accepted in [('pipeline/runs/abc-123/raw/trigger.json', True), ('pipeline/runs/a/b/raw/trigger.json', False)]:
        run_id = key.split('/')[2]
        assert (f'pipeline/runs/{run_id}/raw/trigger.json' == key) is accepted
