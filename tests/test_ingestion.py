import csv
import hashlib
import importlib.util
import io
import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import boto3
from botocore.validate import validate_parameters
import pandas as pd
import pytest

from bda_pipeline.acquisition import HEADER, acquire_dataset, prepare_inputs, validate_fx
from bda_pipeline.ingest_contract import sha, canonical, paths, stage_request, validate_manifest, corridor_query
from bda_pipeline.ingest_control import handle, BUCKET, SITE_BUCKET, release_lock
from bda_pipeline.ingest_compute import baseline_stage, export_stage
from bda_pipeline.analytics import add_behaviour
from bda_pipeline.transform import read_transactions, read_accounts, build_curated_transactions
from bda_pipeline.inference import SCORING_COLUMNS, prepare_inference
from bda_pipeline.model import compare_models, SavedRanker, digest
from bda_pipeline.sagemaker_training import train_channels
from bda_pipeline.treasury_verification import expected_corridors

ROOT = Path(__file__).resolve().parents[1]


def load_script(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def config():
    return dict(image_uri='588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@sha256:' + 'a'*64,
        model_prefix='pipeline/deployments/test/model', model_manifest_sha256='b'*64,
        artifact_prefix='pipeline/deployments/test',
        distribution_id='EU1WB6XM4GKHQ', dashboard_url='https://d1n61tlw9pcrog.cloudfront.net/')


def manifest():
    value = dict(schema_version=1, run_id='test-run', subset='HI-Small', rows=320, train=False, rerun=False,
        start_date='2022-09-01', end_date='2022-09-12', dataset_version=1,
        files={name: dict(bytes=1, sha256='c'*64) for name in ['transactions.csv', 'accounts.csv', 'fx.csv']},
        deployment_sha256=sha(config()), model_manifest_sha256='b'*64, export_dates=None,
        currencies=['US Dollar'], source_scope='synthetic-fixture')
    value['processing_sha256'] = sha({key: value[key] for key in ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version', 'deployment_sha256', 'train', 'export_dates']})
    return value


def raw_fixture(directory):
    directory.mkdir(parents=True)
    with (directory / 'HI-Small_Trans.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        for i in range(320):
            day = [1, 7, 9, 12][i//80]
            writer.writerow([f'2022/09/{day:02d} 12:00', '001', '000A', '1', '000A', i+1,
                'US Dollar', i+1, 'US Dollar', 'ACH' if i%2 == 0 else 'Wire', int(i%7 == 0)])
    (directory / 'HI-Small_accounts.csv').write_text('Bank Name,Bank ID,Account Number,Entity ID,Entity Name\nBank,1,000A,E1,Fixture\n')
    rates = pd.DataFrame([dict(txn_date=str(day.date()), currency='US Dollar', usd_rate=1., rate_date=str(day.date()), source='fixture')
        for day in pd.date_range('2022-08-31', '2022-09-12')])
    rates.to_csv(directory / 'fx.csv', index=False)
    return rates


def test_versioned_download_reuses_only_verified_files(tmp_path):
    cache = tmp_path / 'cache/versions/7'
    raw_fixture(cache)
    calls = []
    def download(handle, path, force_download):
        calls.append((handle, path))
        return cache / path
    first = acquire_dataset(tmp_path / 'dataset', downloader=download)
    assert first['dataset_version'] == 7
    assert calls[1][0].endswith('/versions/7')
    acquire_dataset(tmp_path / 'dataset', downloader=lambda *a, **k: pytest.fail('Unexpected download'))
    (tmp_path / 'dataset/HI-Small_accounts.csv').write_text('tampered')
    acquire_dataset(tmp_path / 'dataset', downloader=download)
    assert len(calls) == 3  # Repair the changed accounts file; retain verified transactions.
    assert calls[-1][1] == 'HI-Small_accounts.csv'
    assert calls[-1][0].endswith('/versions/7')


def test_legacy_copy_unknown_version_and_historical_fx_bounds(tmp_path):
    rates = raw_fixture(tmp_path / 'dataset')
    record = acquire_dataset(tmp_path / 'dataset')
    assert record['dataset_version'] is None
    info = prepare_inputs(tmp_path / 'dataset/HI-Small_Trans.csv', tmp_path / 'dataset/HI-Small_accounts.csv', tmp_path / 'raw', fx_file=tmp_path / 'dataset/fx.csv')
    assert info['rows'] == 320 and info['start_date'] == '2022-09-01'
    assert pd.read_csv(tmp_path / 'raw/transactions.csv').columns[4] == 'Account.1'
    with pytest.raises(ValueError, match='coverage'):
        validate_fx(rates.iloc[1:], pd.Timestamp('2022-08-31'), pd.Timestamp('2022-09-12'), ['US Dollar'])
    with pytest.raises(ValueError, match='stale'):
        validate_fx(rates.assign(rate_date='2022-08-01'), pd.Timestamp('2022-08-31'), pd.Timestamp('2022-09-12'), ['US Dollar'])


def test_manifest_identity_and_sdk_shapes():
    value = manifest()
    validate_manifest(value, sha(config()))
    for changed in [dict(value, rows=321), dict(value, run_id='x; DROP TABLE y')]:
        with pytest.raises(ValueError): validate_manifest(changed, sha(config()))
    request = stage_request('test-run', 'export', config(), {'athena': 'athena-results/q.csv'})
    client = boto3.client('glue', region_name='ap-southeast-1', aws_access_key_id='fixture', aws_secret_access_key='fixture')
    validate_parameters(request, client.meta.service_model.operation_model('StartJobRun').input_shape)
    assert request['WorkerType'] == 'G.2X'
    assert request['Timeout'] == 30
    assert 'run_test_run' in corridor_query('test-run', value['start_date'], value['end_date'])
    with pytest.raises(ValueError, match='ISO'):
        corridor_query('test-run', '2022-09-01', "2022-09-12' OR '1'='1")


def test_export_dates_validation_and_identity(tmp_path):
    base = manifest()
    export_dates = {'dates': ['2022-09-01', '2022-09-07'], 'tail_start': '2022-09-07'}
    identity_fields = ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version', 'deployment_sha256', 'train']
    valid = dict(base, export_dates=export_dates)
    valid['processing_sha256'] = sha({**{key: valid[key] for key in identity_fields}, 'export_dates': export_dates})
    validate_manifest(valid, sha(config()))  # a well-formed, correctly-hashed selection passes
    for bad_dates in [
        {'dates': [], 'tail_start': None},  # empty selection
        {'dates': ['2022-09-01', '2022-09-01'], 'tail_start': None},  # duplicate
        {'dates': ['2022-13-01'], 'tail_start': None},  # not a real date
        {'dates': ['2022-08-01'], 'tail_start': None},  # outside [start_date, end_date]
        {'dates': ['2022-09-01'], 'tail_start': '2022-09-07'},  # tail_start not among dates
        {'dates': 'not-a-list', 'tail_start': None},
    ]:
        broken = dict(base, export_dates=bad_dates)
        broken['processing_sha256'] = sha({**{key: broken[key] for key in identity_fields}, 'export_dates': bad_dates})
        with pytest.raises(ValueError):
            validate_manifest(broken, sha(config()))
    # Different selections are different identities: same base manifest, different export_dates,
    # must not share a processing_sha256 (so the stage cache correctly treats them as distinct runs).
    other_dates = {'dates': ['2022-09-07', '2022-09-09'], 'tail_start': None}
    other = dict(base, export_dates=other_dates)
    other['processing_sha256'] = sha({**{key: other[key] for key in identity_fields}, 'export_dates': other_dates})
    validate_manifest(other, sha(config()))
    assert valid['processing_sha256'] != other['processing_sha256']


class MemoryS3:
    class Missing(Exception): pass
    exceptions = SimpleNamespace(NoSuchKey=Missing)
    def __init__(self): self.data = {}; self.deleted = []
    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        keys = sorted(key for bucket, key in self.data if bucket == Bucket and key.startswith(Prefix))[:MaxKeys]
        return {'KeyCount': len(keys), 'Contents': [{'Key': key} for key in keys]}
    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.data: raise self.Missing()
        body = self.data[Bucket, Key]
        return dict(Body=io.BytesIO(body), ETag=sha(body.decode()))
    def put_object(self, Bucket, Key, Body, **kwargs):
        if kwargs.get('IfNoneMatch') == '*' and (Bucket, Key) in self.data: raise ValueError('Claim conflict')
        if kwargs.get('IfMatch') and kwargs['IfMatch'] != self.get_object(Bucket=Bucket, Key=Key)['ETag']: raise ValueError('ETag conflict')
        self.data[Bucket, Key] = Body
    def head_object(self, Bucket, Key):
        return {'ContentLength': len(self.data[Bucket, Key]), 'ServerSideEncryption': 'AES256'}
    def download_file(self, Bucket, Key, Filename):
        Path(Filename).write_bytes(self.data[Bucket, Key])
    def upload_file(self, Filename, Bucket, Key, **kwargs):
        self.data[Bucket, Key] = Path(Filename).read_bytes()
    def get_paginator(self, operation):
        assert operation == 'list_objects_v2'
        def paginate(Bucket, Prefix):
            items = [{'Key': key, 'Size': len(value)} for (bucket, key), value in sorted(self.data.items())
                     if bucket == Bucket and key.startswith(Prefix)]
            # Multiple pages exercise pagination even with small fixtures.
            return [{'Contents': items[i:i+2]} for i in range(0, len(items), 2)]
        return SimpleNamespace(paginate=paginate)
    def delete_object(self, Bucket, Key, **kwargs):
        assert kwargs['IfMatch'] == self.get_object(Bucket=Bucket, Key=Key)['ETag']
        del self.data[Bucket, Key]; self.deleted.append(Key)


def clients(s3):
    return {name: None for name in ['glue', 'sagemaker', 'athena', 'stepfunctions', 'cloudfront']} | {'s3': s3}


def test_lock_ownership_and_published_retry_cleanup():
    s3 = MemoryS3()
    s3.put_object(Bucket=BUCKET, Key='pipeline/lock.json', Body=canonical({'run_id': 'other-run'}))
    with pytest.raises(ValueError, match='own'): release_lock(s3, 'test-run')
    s3.put_object(Bucket=BUCKET, Key='pipeline/lock.json', Body=canonical({'run_id': 'test-run'}))
    s3.put_object(Bucket=BUCKET, Key=paths('test-run')['raw']+'/manifest.json', Body=canonical(manifest()))
    result = dict(status='published', run_id='test-run')
    s3.put_object(Bucket=BUCKET, Key=paths('test-run')['evidence']+'/result.json', Body=canonical(result))
    assert handle(dict(op='publish', run_id='test-run'), config(), clients(s3)) == result
    assert s3.deleted == ['pipeline/lock.json']
    assert handle(dict(op='publish', run_id='test-run'), config(), clients(s3)) == result


def test_workflow_never_retries_compute_and_has_failure_path():
    module = load_script('infra/ingestion/build-workflow.py')
    workflow = module.workflow()
    def walk(states):
        for state in states.values():
            if state.get('Resource', '').endswith('.sync'): assert 'Retry' not in state
            for branch in state.get('Branches', []): walk(branch['States'])
    walk(workflow['States'])
    assert workflow['States']['ScoreAndAnalyze']['Catch'][0]['Next'] == 'RecordFailure'
    assert json.loads((ROOT / 'infra/ingestion/workflow.asl.json').read_text()) == workflow


def publication_inputs():
    s3 = MemoryS3()
    p = paths('test-run')
    s3.put_object(Bucket=BUCKET, Key='pipeline/lock.json', Body=canonical({'run_id': 'test-run'}))
    s3.put_object(Bucket=BUCKET, Key=p['raw']+'/manifest.json', Body=canonical(manifest()))
    old = canonical({'schema_version': 1, 'run_id': 'previous-run', 'snapshot': 'releases/previous-run/dashboard.json', 'sha256': 'd'*64})
    s3.put_object(Bucket=SITE_BUCKET, Key='dashboard/current.json', Body=old)
    assets = {'index.html': b'<html>fixture</html>', 'dashboard.js': b'console.log(1);', 'dashboard.css': b'body {}',
              'dashboard.json': canonical({'pipeline': {'run_id': 'test-run'}, 'buildStatus': 'complete'})}
    release = dict(pipeline_run_id='test-run', processing_sha256=manifest()['processing_sha256'],
        verification={'scores': {'status': 'passed'}, 'corridors': {'status': 'passed'}},
        files=[dict(file=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()) for name, data in assets.items()])
    s3.put_object(Bucket=BUCKET, Key=p['export']+'/release/manifest.json', Body=canonical(release))
    for name, data in assets.items(): s3.put_object(Bucket=BUCKET, Key=p['export']+'/release/site/'+name, Body=data)
    invalidations = []
    s3.data[BUCKET, p['export']+'/_SUCCESS'] = b''
    job = dict(JobRunState='SUCCEEDED', Arguments=stage_request('test-run', 'export', config())['Arguments'])
    aws = clients(s3) | {'glue': SimpleNamespace(get_job_run=lambda **kw: {'JobRun': job}),
        'cloudfront': SimpleNamespace(create_invalidation=lambda **kw: invalidations.append(kw))}
    return s3, aws, old, invalidations


def test_publication_pointer_commit_and_cache():
    s3, aws, old, invalidations = publication_inputs()
    result = handle({'op': 'publish', 'run_id': 'test-run', 'job_run_id': 'jr-test'}, config(), aws)
    assert json.loads(s3.data[SITE_BUCKET, 'dashboard/current.json'])['previous_run_id'] == 'previous-run'
    assert result['status'] == 'published'
    assert invalidations[0]['InvalidationBatch']['CallerReference'] == 'ingest-test-run'
    assert json.loads(s3.data[BUCKET, 'pipeline/cache/'+manifest()['processing_sha256']+'.json']) == result
    assert 'pipeline/lock.json' in s3.deleted


def test_failed_hash_preserves_previous_release_and_lock():
    s3, aws, old, invalidations = publication_inputs()
    s3.data[BUCKET, paths('test-run')['export']+'/release/site/dashboard.json'] = b'corrupt'
    with pytest.raises(ValueError, match='hash'): handle({'op': 'publish', 'run_id': 'test-run', 'job_run_id': 'jr-test'}, config(), aws)
    assert s3.data[SITE_BUCKET, 'dashboard/current.json'] == old
    assert not invalidations and not s3.deleted


def test_duplicate_run_cannot_claim_and_failure_keeps_outputs():
    s3 = MemoryS3()
    s3.put_object(Bucket=BUCKET, Key='pipeline/lock.json', Body=canonical({'run_id': 'other-run'}))
    s3.put_object(Bucket=BUCKET, Key=paths('test-run')['raw']+'/manifest.json', Body=canonical(manifest()))
    with pytest.raises(ValueError, match='own'): handle({'op': 'begin', 'run_id': 'test-run'}, config(), clients(s3))
    handle({'op': 'failed', 'run_id': 'test-run', 'failure': {'Error': 'test'}}, config(), clients(s3))
    assert json.loads(s3.data[BUCKET, 'pipeline/lock.json'])['run_id'] == 'other-run'


def staged_run(tmp_path, subset, **updates):
    """Raw and curated inputs of the 320-row, four-day fixture, laid out as the baseline stage finds them."""
    inputs = tmp_path / 'input'
    rates = raw_fixture(tmp_path / 'dataset')
    info = prepare_inputs(tmp_path / 'dataset/HI-Small_Trans.csv', tmp_path / 'dataset/HI-Small_accounts.csv', inputs / 'raw', fx_file=tmp_path / 'dataset/fx.csv')
    value = manifest() | info | {'subset': subset} | updates
    frame = build_curated_transactions(read_transactions(inputs / 'raw/transactions.csv'), read_accounts(inputs / 'raw/accounts.csv'), rates)
    frame['transaction_id'] = subset + ':' + frame.source_row_index.astype(str)
    frame['amount_usd_asof'] = frame.amount_usd
    frame = add_behaviour(frame)
    (inputs / 'curated').mkdir()
    frame.to_parquet(inputs / 'curated/data.parquet', index=False)
    (inputs / 'run').mkdir()
    (inputs / 'model').mkdir()
    (inputs / 'model/deployment.json').write_text(json.dumps(config()))
    (inputs / 'run/manifest.json').write_text(json.dumps(value))
    return inputs, value


def serve_inputs(tmp_path, inputs, baseline, value):
    """Stands in for the deployed model, Glue scoring and the Athena corridor query; returns the reference file and Glue's scores."""
    reference = baseline / 'baseline/bq1_scored.parquet'
    model = tmp_path / 'model'
    compare_models(reference, baseline / 'manifest/dashboard.json', model, rounds=3, threads=1)
    prepared = tmp_path / 'prepared'
    prepare_inference(model, reference, prepared, ROOT / 'src/bda_pipeline')
    for path in (prepared / 'artifacts').iterdir(): shutil.copyfile(path, inputs / 'model' / path.name)
    value['model_manifest_sha256'] = digest(inputs / 'model/model-manifest.json')
    value['source_scope'] = 'synthetic-fixture'
    value['deployment_sha256'] = sha(config())
    (inputs / 'run/manifest.json').write_text(json.dumps(value))
    ranker = SavedRanker(inputs / 'model')
    scored = pd.read_parquet(reference)
    cloud = scored[SCORING_COLUMNS].assign(model_score=ranker.predict(scored), txn_date=scored.txn_date)
    (inputs / 'scored').mkdir()
    cloud.to_parquet(inputs / 'scored/data.parquet', index=False)
    (inputs / 'athena').mkdir()
    expected = expected_corridors([scored.assign(txn_date=scored.txn_date.astype(str))], 320)
    expected.to_csv(inputs / 'athena/result.csv', index=False)
    return reference, cloud


@pytest.mark.parametrize('subset', ['HI-Small', 'HI-Medium'])
def test_source_to_release_and_tamper_rejected(tmp_path, subset):
    inputs, value = staged_run(tmp_path, subset)
    baseline = inputs / 'baseline'
    proof = baseline_stage(inputs, baseline, ROOT / 'dashboard')
    assert proof['rows'] == 320
    # Independent per-day breakdown (used by a date-subset export) sums back to the whole population.
    assert sum(day['rows'] for day in proof['daily'].values()) == 320
    assert set(proof['daily']) == {'2022-09-01', '2022-09-07', '2022-09-09', '2022-09-12'}
    reference, cloud = serve_inputs(tmp_path, inputs, baseline, value)
    result = export_stage(inputs, tmp_path / 'export', ROOT / 'dashboard')
    assert result['verification']['scores']['rows'] == 320
    public = json.loads((tmp_path / 'export/release/site/dashboard.json').read_text())
    assert public['pipeline']['run_id'] == 'test-run'
    assert all('is_laundering' not in row for view in public['model']['views'].values() for row in view['queue'])
    if subset == 'HI-Medium':
        assert public['pipeline']['subset'] == subset
        assert 'Transfer evaluation' in public['model']['evaluation_scope']
        cloud.loc[0, 'model_score'] = 0.987654321
        cloud.to_parquet(inputs / 'scored/data.parquet', index=False)
        with pytest.raises(AssertionError): export_stage(inputs, tmp_path / 'bad-export', ROOT / 'dashboard')
        return
    value['train'] = True
    (inputs / 'run/manifest.json').write_text(json.dumps(value))
    trained = tmp_path / 'trained'
    train_channels(baseline / 'baseline', baseline / 'manifest', tmp_path / 'train-work', trained, tmp_path / 'train-report', rounds=3, threads=1, expected_rows=320)
    (inputs / 'candidate').mkdir()
    with tarfile.open(inputs / 'candidate/model.tar.gz', 'w:gz') as archive:
        for path in trained.iterdir(): archive.add(path, arcname='./' + path.name)
    result = export_stage(inputs, tmp_path / 'trained-export', ROOT / 'dashboard')
    assert result['verification']['training']['candidate_only']
    assert not result['verification']['training']['model_promoted']
    cloud.loc[0, 'model_score'] = 0.987654321
    cloud.to_parquet(inputs / 'scored/data.parquet', index=False)
    with pytest.raises(AssertionError): export_stage(inputs, tmp_path / 'bad-export', ROOT / 'dashboard')


def test_training_run_splits_by_row_share_and_verifies_the_candidate(tmp_path):
    inputs, value = staged_run(tmp_path, 'HI-Small', train=True)
    baseline = inputs / 'baseline'
    baseline_stage(inputs, baseline, ROOT / 'dashboard')
    splits = json.loads((baseline / 'manifest/dashboard.json').read_text())['splits']
    # 80 rows on each of four days: train takes the first two days, validation the third and test the fourth.
    assert splits == {'train': ['2022-09-01', '2022-09-09'], 'validation': ['2022-09-09', '2022-09-12'],
                      'test': ['2022-09-12', '2022-09-13']}
    reference, cloud = serve_inputs(tmp_path, inputs, baseline, value)
    trained = tmp_path / 'trained'
    train_channels(baseline / 'baseline', baseline / 'manifest', tmp_path / 'train-work', trained, tmp_path / 'train-report', rounds=3, threads=1, expected_rows=320)
    report = json.loads((trained / 'comparison.json').read_text())
    assert report['splits'] == splits and set(report['metrics']) == {'validation', 'test'}
    (inputs / 'candidate').mkdir()
    with tarfile.open(inputs / 'candidate/model.tar.gz', 'w:gz') as archive:
        for path in trained.iterdir(): archive.add(path, arcname='./' + path.name)
    result = export_stage(inputs, tmp_path / 'export', ROOT / 'dashboard')
    assert result['verification']['training']['candidate_only']
    assert not result['verification']['training']['model_promoted']
    public = json.loads((tmp_path / 'export/release/site/dashboard.json').read_text())
    assert public['splits'] == splits
    assert set(public['aml']) == set(public['model']['views']) == {'validation', 'test'}
    cloud.loc[0, 'model_score'] = 0.987654321
    cloud.to_parquet(inputs / 'scored/data.parquet', index=False)
    with pytest.raises(AssertionError): export_stage(inputs, tmp_path / 'bad-export', ROOT / 'dashboard')


def test_baseline_stage_restricts_to_an_export_dates_selection(tmp_path):
    # row_share_splits needs >= 3 days, so select 3 of the fixture's 4 days (excludes 2022-09-12).
    dates = ['2022-09-01', '2022-09-07', '2022-09-09']
    inputs, value = staged_run(tmp_path, 'HI-Small', train=True, export_dates={'dates': dates, 'tail_start': None})
    baseline = inputs / 'baseline'
    proof = baseline_stage(inputs, baseline, ROOT / 'dashboard')
    # verify_curated still independently verifies the whole raw population...
    assert proof['rows'] == 320
    # ...but baseline/export/training only cover the three selected days (80 rows each).
    scored = pd.read_parquet(baseline / 'baseline/bq1_scored.parquet')
    assert len(scored) == 240
    assert set(scored.txn_date.astype(str)) == set(dates)
    snapshot = json.loads((baseline / 'manifest/dashboard.json').read_text())
    assert snapshot['aml_scope'] == 'date-subset'
    assert snapshot['full_rows'] == 240
    assert snapshot['provenance']['tail_start'] is None
    # Splits are computed only over the selected dates, not the whole 4-day population.
    assert snapshot['splits']['train'][0] == '2022-09-01'
    assert snapshot['splits'][list(snapshot['splits'])[-1]][1] <= '2022-09-10'
    # export/release explicitly skips rather than failing - a date-subset run has no public release.
    result = export_stage(inputs, tmp_path / 'export', ROOT / 'dashboard')
    assert result['status'] == 'skipped'
    assert not (tmp_path / 'export/release').exists()
