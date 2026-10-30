import importlib.util
import io
import json
import tarfile
from pathlib import Path

import numpy as np
import pytest

from bda_pipeline.model import SavedRanker, digest
from bda_pipeline.sagemaker_training import (
    REPOSITORY, build_request, prepare_bundle, train_channels, validate_bundle,
)
from test_model import sample


IMAGE = REPOSITORY + '@sha256:' + 'a' * 64
JOB = 'is459-g3-train-unit'


def bundle(tmp_path):
    frame = sample()
    scored = tmp_path / 'scored.parquet'
    frame.to_parquet(scored, index=False)
    snapshot = tmp_path / 'snapshot.json'
    snapshot.write_text(json.dumps(dict(aml_scope='full', full_rows=len(frame),
        sources=[dict(file='bq1_scored.parquet', sha256=digest(scored))],
        aml={name: dict(metrics=dict(positives=int(frame.iloc[start:end].is_laundering.sum())))
             for name, start, end in [('validation', 80, 160), ('test', 160, 240), ('tail', 240, 320)]})))
    output = tmp_path / 'prepared'
    prepare_bundle(scored, snapshot, output, JOB, IMAGE, rounds=3, max_runtime=120)
    return frame, output


def cli():
    path = Path(__file__).resolve().parents[1] / 'scripts/modeling/sagemaker-training.py'
    spec = importlib.util.spec_from_file_location('training_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_rejects_unbounded_or_unpinned_jobs():
    request = build_request(JOB, IMAGE, 320)
    assert request['EnableNetworkIsolation']
    assert request['ResourceConfig']['InstanceCount'] == 1
    assert request['StoppingCondition']['MaxRuntimeInSeconds'] == 1200
    for arguments in [(JOB, REPOSITORY + ':latest', 320), ('other-job', IMAGE, 320),
                      (JOB, IMAGE, 0), (JOB, IMAGE, 320, 501), (JOB, IMAGE, 320, 120, 1801)]:
        with pytest.raises(ValueError):
            build_request(*arguments)


def test_request_matches_installed_aws_api_schema():
    from botocore.session import Session
    from botocore.validate import validate_parameters
    shape = Session().get_service_model('sagemaker').operation_model('CreateTrainingJob').input_shape
    validate_parameters(build_request(JOB, IMAGE, 320), shape)


def test_tampered_request_and_inputs_fail_before_submission(tmp_path):
    _, output = bundle(tmp_path)
    request = validate_bundle(output)
    request['ResourceConfig']['InstanceCount'] = 20
    (output / 'request.json').write_text(json.dumps(request))
    with pytest.raises(ValueError, match='bounded contract'):
        validate_bundle(output)
    (output / 'manifest/dashboard.json').write_text('{}')
    with pytest.raises((KeyError, ValueError)):
        validate_bundle(output)


def test_sagemaker_channels_train_freeze_and_reload(tmp_path):
    frame, prepared = bundle(tmp_path)
    work, model = tmp_path / 'work', tmp_path / 'model'
    model.mkdir()  # Real SageMaker pre-creates the artifact directory.
    with pytest.raises(ValueError, match='row count'):
        train_channels(prepared / 'baseline', prepared / 'manifest', work, model,
                       tmp_path / 'reports', rounds=3, threads=1, expected_rows=321)
    report = train_channels(prepared / 'baseline', prepared / 'manifest', work, model,
                            tmp_path / 'reports', rounds=3, threads=1, expected_rows=320)
    ranker = SavedRanker(model, threads=1)
    assert report['execution']['job_name'] == JOB
    assert report['selection']['frozen_before_test']
    assert report['total_rows'] == len(frame)
    assert report['execution']['promotion'] == 'candidate-only'
    assert np.isfinite(ranker.predict(frame)).all()
    assert (tmp_path / 'reports/comparison.json').exists()
    assert 'No model promotion, AWS training, or dashboard replacement.' not in report['limitations']


def test_submission_claim_upload_and_exact_request(tmp_path):
    _, prepared = bundle(tmp_path)
    calls = []
    class S3:
        def put_object(self, **kwargs):
            calls.append(('claim', kwargs))
        def upload_file(self, *args, **kwargs):
            calls.append(('upload', args))
    class SageMaker:
        def create_training_job(self, **kwargs):
            calls.append(('create', kwargs))
            return {'TrainingJobArn': 'arn:test'}
    cli().submit(prepared, S3(), SageMaker())
    assert [call[0] for call in calls] == ['claim', 'upload', 'upload', 'upload', 'create']
    assert calls[0][1]['IfNoneMatch'] == '*'
    assert calls[-1][1] == validate_bundle(prepared)


def test_failed_claim_does_not_upload_or_start_compute(tmp_path):
    _, prepared = bundle(tmp_path)
    class S3:
        def put_object(self, **kwargs):
            raise RuntimeError('Already claimed')
        def upload_file(self, *args, **kwargs):
            pytest.fail('Must not overwrite staged inputs')
    class SageMaker:
        def create_training_job(self, **kwargs):
            pytest.fail('Must not start duplicate compute')
    with pytest.raises(RuntimeError, match='claimed'):
        cli().submit(prepared, S3(), SageMaker())


def test_completed_artifact_collection_checks_provenance_and_reloads(tmp_path):
    _, prepared = bundle(tmp_path)
    model = tmp_path / 'trained'
    train_channels(prepared / 'baseline', prepared / 'manifest', tmp_path / 'work', model,
                   tmp_path / 'reports', rounds=3, threads=1)
    archive = tmp_path / 'trained.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for path in model.iterdir():
            tar.add(path, arcname='./' + path.name)
    request = validate_bundle(prepared)
    class S3:
        def download_file(self, bucket, key, destination):
            import shutil
            shutil.copyfile(archive, destination)
    class SageMaker:
        def describe_training_job(self, **kwargs):
            return {**request, 'TrainingJobStatus': 'Completed', 'TrainingTimeInSeconds': 4,
                    'ResourceConfig': {**request['ResourceConfig'], 'KeepAlivePeriodInSeconds': 0},
                    'ModelArtifacts': {'S3ModelArtifacts':
                        f's3://is459-g3-588738581042-ap-southeast-1/sagemaker/outputs/{JOB}/output/model.tar.gz'}}
    result = cli().collect(prepared, tmp_path / 'collected', S3(), SageMaker())
    assert result['status'] == 'completed-artifacts-verified-candidate-only'
    assert result['training_seconds'] == 4
    assert not result['model_promoted']


@pytest.mark.parametrize('name', ['../escape.json', '/absolute.json', 'nested/file.json', 'x\\escape.json'])
def test_artifact_extraction_blocks_paths(tmp_path, name):
    archive = tmp_path / 'model.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        entry = tarfile.TarInfo(name)
        entry.size = 2
        tar.addfile(entry, io.BytesIO(b'{}'))
    with pytest.raises(ValueError, match='Unsafe'):
        cli().extract_model(archive, tmp_path / 'extract')
    assert not (tmp_path / 'extract').exists()
