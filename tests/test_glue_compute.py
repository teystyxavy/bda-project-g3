"""Glue transfer/commit smoke plus failed publication and cancellation guards."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bda_pipeline.glue_compute import download_input, run_stage, safe_relative
from bda_pipeline.ingest_contract import BUCKET, canonical, paths, sha, stage_request
from bda_pipeline.ingest_control import assert_glue_stage, no_active_workers, stop_owned_workers
from test_ingestion import MemoryS3, config, manifest


def staged_inputs():
    s3, cfg, value = MemoryS3(), config(), manifest()
    p = paths(value['run_id'])
    s3.data[BUCKET, p['raw'] + '/manifest.json'] = canonical(value)
    s3.data[BUCKET, p['raw'] + '/transactions.csv'] = b'fixture'
    s3.data[BUCKET, p['curated'] + '/txn_date=2022-09-01/part.parquet'] = b'fixture'
    s3.data[BUCKET, cfg['model_prefix'] + '/deployment.json'] = canonical(cfg)
    return s3, cfg, value


def run(s3, cfg, tmp_path, stage='baseline', **kwargs):
    request = stage_request('test-run', stage, cfg, {'athena': 'athena-results/query.csv'} if stage == 'export' else None)
    args = request['Arguments']
    return run_stage(s3, 'test-run', stage, json.loads(args['--INPUTS_JSON']), sha(cfg),
                     args['--OUTPUT'], Path(__file__).resolve().parents[1] / 'dashboard', tmp_path / 'work', **kwargs)


def test_staging_and_commit_marker_last(monkeypatch, tmp_path):
    s3, cfg, _ = staged_inputs()
    from botocore.session import Session
    from botocore.validate import validate_parameters
    shape = Session().get_service_model('s3').operation_model('PutObject').input_shape
    original_put = s3.put_object
    def put(**kwargs):
        # In the Glue container this validates against its older bundled SDK.
        validate_parameters(kwargs, shape)
        return original_put(**kwargs)
    s3.put_object = put
    uploads = []
    original = s3.upload_file
    def upload(file, bucket, key, **kwargs):
        assert (BUCKET, paths('test-run')['baseline'] + '/_SUCCESS') not in s3.data
        uploads.append(key)
        original(file, bucket, key, **kwargs)
    s3.upload_file = upload
    def baseline(inputs, output, assets):
        assert (inputs / 'curated/txn_date=2022-09-01/part.parquet').read_bytes() == b'fixture'
        assert json.loads((inputs / 'model/deployment.json').read_text()) == cfg
        output.mkdir()
        (output / 'proof.json').write_bytes(canonical({'status': 'passed'}))
        return {'status': 'passed'}
    monkeypatch.setattr('bda_pipeline.ingest_compute.baseline_stage', baseline)
    assert run(s3, cfg, tmp_path) == {'status': 'passed'}
    assert uploads == ['pipeline/runs/test-run/baseline/proof.json']
    assert s3.data[BUCKET, 'pipeline/runs/test-run/baseline/_SUCCESS'] == b''
    with pytest.raises(ValueError, match='already exists'):
        run(s3, cfg, tmp_path)


@pytest.mark.parametrize('failure', ['compute', 'upload'])
def test_failed_stage_never_commits(monkeypatch, tmp_path, failure):
    s3, cfg, _ = staged_inputs()
    def baseline(inputs, output, assets):
        output.mkdir()
        (output / 'proof.json').write_text('{}')
        if failure == 'compute':
            raise RuntimeError('compute failed')
    def upload(*args, **kwargs):
        raise RuntimeError('upload failed')
    monkeypatch.setattr('bda_pipeline.ingest_compute.baseline_stage', baseline)
    s3.upload_file = upload
    with pytest.raises(RuntimeError):
        run(s3, cfg, tmp_path)
    assert (BUCKET, 'pipeline/runs/test-run/baseline/_SUCCESS') not in s3.data


def test_rejects_channels_and_wrong_deployment(tmp_path):
    s3, cfg, _ = staged_inputs()
    cfg['dashboard_url'] = 'changed'
    with pytest.raises(ValueError, match='Deployment changed'):
        run(s3, cfg, tmp_path)
    for value in ['../escape', 'x/../../escape', '/absolute', 'x\\escape']:
        with pytest.raises(ValueError):
            safe_relative(value)


def test_download_rejects_traversal_from_remote_prefix(tmp_path):
    s3 = MemoryS3()
    s3.data[BUCKET, 'pipeline/../escape'] = b'bad'
    with pytest.raises(ValueError, match='Unsafe'):
        download_input(s3, 'pipeline/', tmp_path)


def test_completion_requires_owned_success_and_marker():
    s3, cfg, _ = staged_inputs()
    job = dict(JobRunState='RUNNING', Arguments=stage_request('test-run', 'baseline', cfg)['Arguments'])
    glue = SimpleNamespace(get_job_run=lambda **kw: {'JobRun': job})
    with pytest.raises(ValueError, match='identity or completion'):
        assert_glue_stage(glue, s3, 'test-run', 'baseline', 'jr-id', cfg)
    job['JobRunState'] = 'SUCCEEDED'
    with pytest.raises(KeyError):
        assert_glue_stage(glue, s3, 'test-run', 'baseline', 'jr-id', cfg)
    s3.data[BUCKET, paths('test-run')['baseline'] + '/_SUCCESS'] = b''
    assert_glue_stage(glue, s3, 'test-run', 'baseline', 'jr-id', cfg)
    job['Arguments']['--RUN_ID'] = 'other-run'
    with pytest.raises(ValueError):
        assert_glue_stage(glue, s3, 'test-run', 'baseline', 'jr-id', cfg)


@pytest.mark.parametrize('stage', ['baseline', 'export'])
def test_new_glue_workers_are_cancelled_and_block_unlock(stage):
    stopped = []
    def pages(JobName):
        return [{'JobRuns': [dict(Id='owned', JobRunState='RUNNING',
            Arguments={'--OUTPUT': f's3://{BUCKET}/pipeline/runs/test-run/{stage}/'})]}] if JobName.endswith(stage) else [{'JobRuns': []}]
    clients = {'glue': SimpleNamespace(get_paginator=lambda op: SimpleNamespace(paginate=pages),
        batch_stop_job_run=lambda **kw: stopped.append(kw) or {}),
        'sagemaker': SimpleNamespace(get_paginator=lambda op: SimpleNamespace(paginate=lambda **kw: []))}
    with pytest.raises(ValueError, match='Glue worker'):
        no_active_workers(clients, 'test-run')
    assert stop_owned_workers(clients, 'test-run') == ['owned']
    assert stopped[0]['JobName'] == 'is459-g3-ingest-' + stage


@pytest.mark.parametrize('field', ['JobRunId', 'JobRunID', 'Id'])
def test_glue_sync_result_identity(field):
    from bda_pipeline.ingest_control import stage_run_id
    assert stage_run_id({'job_result': {field: 'jr-owned'}}) == 'jr-owned'
    with pytest.raises(ValueError, match='Missing Glue'):
        stage_run_id({'job_result': {}})


@pytest.mark.parametrize('subset', ['HI-Small', 'HI-Medium'])
def test_real_baseline_through_s3_boundary(tmp_path, subset):
    """Run actual independent verification/rules after paginated S3 staging."""
    from test_ingestion import raw_fixture
    from bda_pipeline.acquisition import prepare_inputs
    from bda_pipeline.analytics import add_behaviour
    from bda_pipeline.transform import read_transactions, read_accounts, build_curated_transactions
    rates = raw_fixture(tmp_path / 'dataset')
    raw = tmp_path / 'raw'
    info = prepare_inputs(tmp_path / 'dataset/HI-Small_Trans.csv',
        tmp_path / 'dataset/HI-Small_accounts.csv', raw, fx_file=tmp_path / 'dataset/fx.csv')
    frame = build_curated_transactions(read_transactions(raw / 'transactions.csv'),
                                       read_accounts(raw / 'accounts.csv'), rates)
    frame['transaction_id'] = subset + ':' + frame.source_row_index.astype(str)
    frame['amount_usd_asof'] = frame.amount_usd
    frame = add_behaviour(frame)
    data = tmp_path / 'curated.parquet'
    frame.to_parquet(data, index=False)
    s3, cfg, value = staged_inputs()
    value.update(info, subset=subset)
    fields = ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version', 'deployment_sha256', 'train']
    value['processing_sha256'] = sha({key: value[key] for key in fields})
    p = paths('test-run')
    s3.data[BUCKET, p['raw'] + '/manifest.json'] = canonical(value)
    for name in value['files']:
        s3.data[BUCKET, p['raw'] + '/' + name] = (raw / name).read_bytes()
    del s3.data[BUCKET, p['curated'] + '/txn_date=2022-09-01/part.parquet']
    s3.data[BUCKET, p['curated'] + '/data.parquet'] = data.read_bytes()
    proof = run(s3, cfg, tmp_path)
    assert proof['status'] == 'passed' and proof['rows'] == 320
    snapshot = json.loads(s3.data[BUCKET, p['baseline'] + '/manifest/dashboard.json'])
    assert snapshot['provenance']['scoring_location'] == 'aws-glue rule baseline'
    assert (BUCKET, p['baseline'] + '/baseline/bq1_scored.parquet') in s3.data
    # Input tampering must fail before committing any stage output.
    s3.data = {(bucket, key): body for (bucket, key), body in s3.data.items()
               if not key.startswith(p['baseline'] + '/')}
    s3.data[BUCKET, p['raw'] + '/transactions.csv'] += b'tampered'
    with pytest.raises((ValueError, AssertionError)):
        run(s3, cfg, tmp_path / 'tampered')
    assert (BUCKET, p['baseline'] + '/_SUCCESS') not in s3.data
