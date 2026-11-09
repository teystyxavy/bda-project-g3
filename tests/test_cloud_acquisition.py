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


def fixture(tmp_path, subset='HI-Small'):
    directory = tmp_path / subset / 'cache/versions/7'
    directory.mkdir(parents=True)
    with (directory / f'{subset}_Trans.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        for i in range(20):
            writer.writerow(['2022/09/01 12:00', '001', '000A', '002', '000B', i+1,
                'US Dollar', i+1, 'US Dollar', 'Wire', 0])
    (directory / f'{subset}_accounts.csv').write_text('Bank Name,Bank ID,Account Number,Entity ID,Entity Name\nBank,001,000A,E1,Fixture\n')
    def downloader(handle, path, force_download):
        assert handle.endswith('/versions/7') or path == f'{subset}_Trans.csv'
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
            assert set(event) == {'run_id', 'rows', 'dataset_version', 'train', 'refresh', 'rerun', 'subset'}
            return {'executionArn': 'cloud-execution'}
    args = SimpleNamespace(fx_file=None, deployment=None, rows=1000, train=False,
        dataset_version=None, run_id='cloud-launch', refresh=False, rerun=False)
    assert cli.cloud_start(args, States())['acquisition_location'] == 'aws-lambda'
    workflow = json.loads((root / 'infra/cloud-ingestion/workflow.asl.json').read_text())
    assert workflow['States']['Batch']['Resource'].endswith('startExecution.sync:2')
    assert 'Retry' not in workflow['States']['Acquire'] and 'Retry' not in workflow['States']['Batch']


def test_medium_cache_is_isolated_and_reused(tmp_path):
    s3 = S3()
    for subset in ['HI-Small', 'HI-Medium']:
        root = tmp_path / subset; root.mkdir()
        acquire({'run_id': subset.lower() + '-cloud', 'subset': subset, 'rows': 10,
                 'dataset_version': 7}, {'s3': s3}, root, downloader=fixture(tmp_path / 'fixtures', subset))
    small = json.loads(s3.data['pipeline/acquisition/latest.json'])
    medium = json.loads(s3.data['pipeline/acquisition/HI-Medium/latest.json'])
    assert small['subset'] == 'HI-Small' and medium['subset'] == 'HI-Medium'
    manifest = json.loads(s3.data['pipeline/runs/hi-medium-cloud/raw/manifest.json'])
    assert manifest['subset'] == 'HI-Medium'
    validate_manifest(manifest, sha(json.loads(s3.data[CONFIG_KEY])))
    root = tmp_path / 'reuse'; root.mkdir()
    def no_download(*args, **kwargs): raise AssertionError('Kaggle download attempted')
    result = acquire({'run_id': 'medium-reuse', 'subset': 'HI-Medium', 'rows': 10,
                      'dataset_version': 7}, {'s3': s3}, root, downloader=no_download)
    assert result['rows'] == 10
    reused = json.loads(s3.data['pipeline/runs/medium-reuse/raw/manifest.json'])
    assert reused['processing_sha256'] == manifest['processing_sha256']
    assert reused['dataset_origin'] == 'verified-s3-kaggle-reuse'


@pytest.mark.parametrize('options', [
    {'subset': 'HI-Medium', 'rows': 32_000_001}, {'subset': 'HI-Medium', 'rows': True},
    {'subset': 'HI-Medium', 'rows': 10, 'train': True},
    {'subset': 'LI-Medium', 'rows': 10}, {'subset': 'HI-Large', 'rows': 10},
])
def test_medium_bounds(options):
    with pytest.raises(ValueError): request({'run_id': 'medium-bounds', **options})


def test_medium_launcher_passes_subset_without_local_data():
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('medium_cli', root / 'scripts/ingestion/ingest.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    class States:
        def start_execution(self, **kwargs):
            event = json.loads(kwargs['input'])
            assert event['subset'] == 'HI-Medium' and event['rows'] == 1000
            return {'executionArn': 'medium-execution'}
    args = SimpleNamespace(fx_file=None, deployment=None, subset='HI-Medium', rows=1000,
        train=False, dataset_version=8, run_id='medium-launch', refresh=False, rerun=False)
    assert cli.cloud_start(args, States())['execution_arn'] == 'medium-execution'


@pytest.mark.parametrize('rows', [None, 1001, 31_898_238, 32_000_000])
def test_medium_accepts_full_and_custom_limits(rows):
    assert request({'run_id': 'full-medium', 'subset': 'HI-Medium', 'rows': rows})['rows'] == rows


def test_full_medium_acquisition_prepares_all_rows(tmp_path):
    downloader = fixture(tmp_path, 'HI-Medium')
    root = tmp_path / 'full-medium'
    root.mkdir()
    (root / 'kaggle-cache').mkdir()
    (root / 'kaggle-cache' / 'duplicate').write_text('download cache')
    s3 = S3()
    result = acquire({'run_id': 'full-medium', 'subset': 'HI-Medium', 'dataset_version': 7},
                     {'s3': s3}, root, downloader=downloader)
    manifest = json.loads(s3.data['pipeline/runs/full-medium/raw/manifest.json'])
    assert result['rows'] == 20
    assert not (root / 'kaggle-cache').exists()
    assert (root / 'dataset' / 'HI-Medium_Trans.csv').is_file()
    assert manifest['source_scope'] == 'selected-subset'
    assert manifest['subset'] == 'HI-Medium'
    assert manifest['train'] is False


# --- HI-Large cache-only runs ------------------------------------------------
from bda_pipeline import cloud_acquisition as cloud


class ReadOnlyS3(S3):
    def put_object(self, *args, **kwargs): raise AssertionError('cache-only runs must not write')
    def upload_file(self, *args, **kwargs): raise AssertionError('cache-only runs must not upload')
    def download_file(self, *args, **kwargs): raise AssertionError('cache-only runs must not download')


def pinned_store(monkeypatch, **manifest_changes):
    s3 = ReadOnlyS3()
    config = json.loads(s3.data[CONFIG_KEY])
    manifest = {'subset': 'HI-Large', 'run_id': 'hi-large-delta1-20261107-batch', 'rows': 179702229,
                'files': {'transactions.csv': {'sha256': 'f'*64}}, 'start_date': '2022-09-01', 'end_date': '2022-11-05',
                'dataset_version': 8, 'deployment_sha256': sha(config), 'train': False, 'export_dates': None, **manifest_changes}
    identity = {k: manifest[k] for k in ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version', 'deployment_sha256', 'train']}
    identity['export_dates'] = manifest['export_dates']
    manifest['processing_sha256'] = sha(identity)
    monkeypatch.setitem(cloud.CACHE_ONLY_RUNS, 'HI-Large', {'run_id': manifest['run_id'], 'processing_sha256': manifest['processing_sha256']})
    manifest_key, cache_key = cloud.cache_only_keys('HI-Large')
    s3.data[manifest_key] = json.dumps(manifest).encode()
    s3.data[cache_key] = json.dumps({'status': 'published', 'run_id': manifest['run_id'],
                                     'processing_sha256': manifest['processing_sha256'], 'release_url': 'x'}).encode()
    return s3


def test_hi_large_reuses_pinned_published_run_without_writing(tmp_path, monkeypatch):
    s3 = pinned_store(monkeypatch)
    result = acquire({'run_id': 'ui-aaaaaaaaaaaa-bbbbbbbbbbbb', 'subset': 'HI-Large'}, {'s3': s3}, tmp_path)
    assert result['cache_hit'] is True
    assert result['result']['run_id'] == 'hi-large-delta1-20261107-batch'
    assert result['result']['requested_run_id'] == 'ui-aaaaaaaaaaaa-bbbbbbbbbbbb'
    assert not any('ui-aaaaaaaaaaaa' in key for key in s3.data)


@pytest.mark.parametrize('tamper', ['deployment', 'identity', 'unpublished', 'missing_cache', 'missing_manifest'])
def test_hi_large_fails_instead_of_reprocessing(tmp_path, monkeypatch, tamper):
    s3 = pinned_store(monkeypatch)
    manifest_key, cache_key = cloud.cache_only_keys('HI-Large')
    if tamper == 'deployment': s3.data[CONFIG_KEY] = json.dumps({'model_manifest_sha256': 'c'*64}).encode()
    if tamper == 'identity':
        m = json.loads(s3.data[manifest_key]); m['rows'] = 1; s3.data[manifest_key] = json.dumps(m).encode()
    if tamper == 'unpublished':
        c = json.loads(s3.data[cache_key]); c['status'] = 'failed'; s3.data[cache_key] = json.dumps(c).encode()
    if tamper == 'missing_cache': del s3.data[cache_key]
    if tamper == 'missing_manifest': del s3.data[manifest_key]
    with pytest.raises(ValueError):
        acquire({'run_id': 'ui-aaaaaaaaaaaa-bbbbbbbbbbbb', 'subset': 'HI-Large'}, {'s3': s3}, tmp_path)


@pytest.mark.parametrize('changes', [{'rows': 10}, {'rerun': True}, {'refresh': True}, {'train': True}, {'dataset_version': 8}])
def test_hi_large_refuses_processing_options(changes):
    with pytest.raises(ValueError):
        request({'run_id': 'ui-aaaaaaaaaaaa-bbbbbbbbbbbb', 'subset': 'HI-Large', **changes})


def test_cache_hit_activates_the_reused_release_before_ending():
    workflow = json.loads((Path(__file__).resolve().parents[1] / 'infra/cloud-ingestion/workflow.asl.json').read_text())
    assert workflow['States']['Cached']['Choices'][0]['Next'] == 'ExistingRelease'
    state = workflow['States']['ExistingRelease']
    # The dashboard detects a cache hit by this state name, so it must stay.
    assert state['Type'] == 'Task' and state['Resource'] == '${control_arn}'
    assert state['Parameters'] == {'op': 'activate', 'run_id.$': '$.acquisition.result.run_id', 'requested_run_id.$': '$.run_id'}
    # A failed pointer switch fails the execution; only Lambda service errors are retried.
    assert state['Catch'][0]['ErrorEquals'] == ['States.ALL'] and state['Catch'][0]['Next'] == 'NotifyFailure'
    assert all(error.startswith('Lambda.') for retry in state['Retry'] for error in retry['ErrorEquals'])
    assert workflow['States'][state['Next']] == {'Type': 'Pass', 'OutputPath': '$.acquisition.result', 'End': True}
