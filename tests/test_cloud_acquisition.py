import csv
import importlib.util
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from bda_pipeline.acquisition import HEADER
from bda_pipeline.cloud_acquisition import acquire, request, kaggle_environment
from bda_pipeline.ingest_contract import CONFIG_KEY, validate_manifest, sha


class S3:
    def __init__(self):
        self.data = {CONFIG_KEY: json.dumps({'model_manifest_sha256': 'b'*64}).encode()}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {'Contents': [{'Key': key} for key in sorted(self.data) if key.startswith(Prefix)][:MaxKeys]}

    def get_object(self, Bucket, Key): return {'Body': io.BytesIO(self.data[Key])}

    def put_object(self, Bucket, Key, Body, **kwargs):
        if kwargs.get('IfNoneMatch') == '*' and Key in self.data: raise ValueError('Existing input claim')
        self.data[Key] = Body

    def upload_file(self, Filename, Bucket, Key, **kwargs): self.data[Key] = Path(Filename).read_bytes()

    def download_file(self, Bucket, Key, Filename, **kwargs): Path(Filename).write_bytes(self.data[Key])


def fixture(tmp_path):
    directory = tmp_path / 'cache/versions/7'
    directory.mkdir(parents=True)
    with (directory / 'HI-Small_Trans.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        for i in range(20):
            writer.writerow(['2022/09/01 12:00', '001', '000A', '002', '000B', i+1,
                'US Dollar', i+1, 'US Dollar', 'Wire', 0])
    (directory / 'HI-Small_accounts.csv').write_text('Bank Name,Bank ID,Account Number,Entity ID,Entity Name\nBank,001,000A,E1,Fixture\n')
    def downloader(handle, path, force_download):
        assert handle.endswith('/versions/7') or path == 'HI-Small_Trans.csv'
        return directory / path
    return downloader


def test_cloud_acquisition_commits_manifest_last_and_reuses_verified_source_and_release(tmp_path):
    s3 = S3()
    root = tmp_path / 'first'; root.mkdir()
    result = acquire({'run_id': 'cloud-first', 'rows': 10}, {'s3': s3}, root, downloader=fixture(tmp_path))
    assert result == {'cache_hit': False, 'run_id': 'cloud-first', 'rows': 10, 'dataset_version': 7}
    key = 'pipeline/runs/cloud-first/raw/manifest.json'
    assert list(s3.data)[-1] == key
    manifest = json.loads(s3.data[key]); validate_manifest(manifest, sha(json.loads(s3.data[CONFIG_KEY])))
    assert manifest['acquisition_location'] == 'aws-lambda'
    assert manifest['dataset_origin'] == 'kaggle-api'
    cache_key = 'pipeline/cache/' + manifest['processing_sha256'] + '.json'
    s3.data[cache_key] = json.dumps({'status': 'published', 'run_id': 'cloud-first'}).encode()
    root = tmp_path / 'second'; root.mkdir()
    def no_download(*args, **kwargs): raise AssertionError('Verified S3 cache caused Kaggle download')
    cached = acquire({'run_id': 'cloud-second', 'rows': 10}, {'s3': s3}, root, downloader=no_download)
    assert cached['cache_hit'] and cached['result']['run_id'] == 'cloud-first'
    assert 'pipeline/runs/cloud-second/raw/manifest.json' not in s3.data
    root = tmp_path / 'duplicate'; root.mkdir()
    with pytest.raises(ValueError, match='Existing input claim'):
        acquire({'run_id': 'cloud-first'}, {'s3': s3}, root)


def test_credentials_restored_and_bad_fx_never_commits_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv('KAGGLE_API_TOKEN', 'prior-value')
    class Secrets:
        def get_secret_value(self, SecretId): return {'SecretString': json.dumps({'api_token': 'runtime-value'})}
    with kaggle_environment(tmp_path, Secrets(), 'secret-arn'):
        assert os.environ['KAGGLE_API_TOKEN'] == 'runtime-value'
    assert os.environ['KAGGLE_API_TOKEN'] == 'prior-value'
    s3 = S3(); root = tmp_path / 'failed'; root.mkdir()
    def failed_prepare(*args, **kwargs): raise ValueError('Missing historical FX')
    monkeypatch.setattr('bda_pipeline.cloud_acquisition.prepare_inputs', failed_prepare)
    with pytest.raises(ValueError, match='Missing historical FX'):
        acquire({'run_id': 'cloud-failed'}, {'s3': s3}, root, downloader=fixture(tmp_path))
    assert not any(key.endswith('manifest.json') for key in s3.data)


@pytest.mark.parametrize('event', [
    {'run_id': 'bad-id', 'train': True, 'rows': 10}, {'run_id': 'bad-id', 'rows': True},
    {'run_id': 'bad-id', 'dataset_version': 0}, {'run_id': 'bad-id', 'train': 'yes'},
    {'run_id': 'bad-id', 'api_token': 'never-accepted'}])
def test_cloud_options_reject_unsupported_or_unsafe_inputs(event):
    with pytest.raises(ValueError): request(event)


def test_launcher_starts_cloud_workflow_without_accessing_local_data():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('cloud_cli', root / 'scripts/ingestion/ingest.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    class States:
        class exceptions:
            class StateMachineDoesNotExist(Exception): pass
        def start_execution(self, **kwargs):
            assert kwargs['stateMachineArn'].endswith(':stateMachine:is459-g3-cloud-ingest')
            event = json.loads(kwargs['input'])
            assert event['rows'] == 1000 and event['train'] is False
            assert set(event) == {'run_id', 'rows', 'dataset_version', 'train', 'refresh', 'rerun'}
            return {'executionArn': 'cloud-execution'}
    args = SimpleNamespace(fx_file=None, deployment=None, rows=1000, train=False,
        dataset_version=None, run_id='cloud-launch', refresh=False, rerun=False)
    assert cli.cloud_start(args, States())['acquisition_location'] == 'aws-lambda'
    workflow = json.loads((root / 'infra/cloud-ingestion/workflow.asl.json').read_text())
    assert workflow['States']['Batch']['Resource'].endswith('startExecution.sync:2')
    assert 'Retry' not in workflow['States']['Acquire'] and 'Retry' not in workflow['States']['Batch']
