"""Cache correctness, dependency gates, bounded waits and owned cancellation."""
import json
from types import SimpleNamespace

import pytest

from bda_pipeline.ingest_contract import BUCKET, canonical, paths, processing_request, sha
from bda_pipeline.ingest_control import get_json, put_json, stop_owned_workers
from bda_pipeline.stage_cache import register, reuse, stage_key
from test_ingestion import MemoryS3, config, manifest, load_script


class CacheS3(MemoryS3):
    def __init__(self):
        super().__init__()
        self.copied = []

    def head_object(self, Bucket, Key):
        return {'ContentLength': len(self.data[Bucket, Key]), 'ETag': self.get_object(Bucket=Bucket, Key=Key)['ETag'], 'ServerSideEncryption': 'AES256'}

    def get_paginator(self, operation):
        assert operation == 'list_objects_v2'
        def paginate(Bucket, Prefix):
            return [{'Contents': [{'Key': key, 'Size': len(data), 'ETag': self.head_object(Bucket=Bucket, Key=key)['ETag']}
                    for (bucket, key), data in self.data.items() if bucket == Bucket and key.startswith(Prefix)]}]
        return SimpleNamespace(paginate=paginate)

    def copy_object(self, Bucket, Key, CopySource, CopySourceIfMatch, **kwargs):
        assert self.head_object(**CopySource)['ETag'] == CopySourceIfMatch
        self.data[Bucket, Key] = self.data[CopySource['Bucket'], CopySource['Key']]
        self.copied.append(Key)


NETWORK_FILES = ['candidates/part-0.parquet', 'loops/part-0.parquet', 'ring_members/part-0.parquet']


def prepared():
    cfg = config() | {'stage_versions': {'curated': 'curate-v1', 'scored': 'score-v1', 'network': 'network-v1'}}
    original = manifest() | {'deployment_sha256': sha(cfg)}
    identity_fields = ['subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version', 'deployment_sha256', 'train']
    original['processing_sha256'] = sha({key: original[key] for key in identity_fields})
    s3 = CacheS3()
    p = paths(original['run_id'])
    for suffix, value in [('raw/manifest.json', original), ('evidence/result.json', {'status': 'published'}),
                          ('export/release/manifest.json', {'verification': {'scores': {'status': 'passed'}}}),
                          ('baseline/proof.json', {'status': 'passed', 'rows': original['rows']})]:
        put_json(s3, 'pipeline/runs/test-run/' + suffix, value)
    for stage in ['curated', 'scored']:
        s3.put_object(Bucket=BUCKET, Key=p[stage] + '/txn_date=2022-09-01/data.parquet', Body=b'fixture')
        s3.put_object(Bucket=BUCKET, Key=p[stage] + '/_SUCCESS', Body=b'')
        register(s3, stage, original, cfg, get_json, put_json)
    # The network job writes one folder per table (each with Spark's own
    # _SUCCESS) and a root _SUCCESS once every table exists.
    for name in NETWORK_FILES:
        s3.put_object(Bucket=BUCKET, Key=f"{p['network']}/{name}", Body=b'network-' + name.encode())
        s3.put_object(Bucket=BUCKET, Key=f"{p['network']}/{name.split('/')[0]}/_SUCCESS", Body=b'')
    s3.put_object(Bucket=BUCKET, Key=p['network'] + '/_SUCCESS', Body=b'')
    register(s3, 'network', original, cfg, get_json, put_json)
    return s3, cfg, original


def test_cache_uses_semantic_dependencies_and_model_change_reuses_only_curated():
    s3, cfg, original = prepared()
    changed = original | {'run_id': 'next-run', 'train': True, 'model_manifest_sha256': 'd' * 64}
    assert reuse(s3, 'curated', changed, cfg, get_json)['hit']
    assert not reuse(s3, 'scored', changed, cfg, get_json)['hit']
    assert s3.copied[-1].endswith('/_SUCCESS')
    assert stage_key('curated', original, cfg) != stage_key('curated', original | {'files': original['files'] | {'fx.csv': {'sha256': 'e'*64, 'bytes': 1}}}, cfg)
    assert stage_key('curated', original, cfg) != stage_key('curated', original, cfg | {'stage_versions': {'curated': 'curate-v2', 'scored': 'score-v1'}})


@pytest.mark.parametrize('tamper', ['parquet', 'proof', 'receipt', 'missing-marker'])
def test_corrupt_cache_is_rejected_before_copy(tamper):
    s3, cfg, original = prepared()
    p = paths('test-run')
    if tamper == 'parquet': s3.data[BUCKET, p['curated'] + '/txn_date=2022-09-01/data.parquet'] = b'corrupt'
    elif tamper == 'proof': put_json(s3, p['baseline'] + '/proof.json', {'status': 'failed'})
    elif tamper == 'missing-marker': del s3.data[BUCKET, p['curated'] + '/_SUCCESS']
    else:
        key = 'pipeline/stage-cache/curated/' + stage_key('curated', original, cfg) + '.json'
        receipt, _ = get_json(s3, key)
        receipt['objects'][0]['etag'] = 'tampered'
        put_json(s3, key, receipt)
    with pytest.raises(ValueError): reuse(s3, 'curated', original | {'run_id': 'next-run'}, cfg, get_json)
    assert not s3.copied


def test_rerun_bypasses_stage_cache_and_identical_scores_can_be_reused():
    s3, cfg, original = prepared()
    assert not reuse(s3, 'scored', original | {'run_id': 'next-run', 'rerun': True}, cfg, get_json)['hit']
    assert reuse(s3, 'scored', original | {'run_id': 'next-run', 'train': True}, cfg, get_json)['hit']
    assert s3.copied[-1].endswith('/_SUCCESS')


def test_network_cache_survives_model_change_and_copies_every_table_marker_last():
    s3, cfg, original = prepared()
    changed = original | {'run_id': 'next-run', 'train': True, 'model_manifest_sha256': 'd' * 64}
    result = reuse(s3, 'network', changed, cfg, get_json)
    assert result['hit'] and result['objects'] == len(NETWORK_FILES) + 1
    destination = paths('next-run')['network']
    assert sorted(s3.copied[:-1]) == [f'{destination}/{name}' for name in NETWORK_FILES]
    assert s3.copied[-1] == destination + '/_SUCCESS'
    # Per-table Spark markers are not part of the inventory, so are never copied.
    assert not any(key.endswith('/candidates/_SUCCESS') for key in s3.copied)


def test_network_cache_key_follows_curated_and_network_versions_only():
    _, cfg, original = prepared()
    key = stage_key('network', original, cfg)
    versions = cfg['stage_versions']
    assert key != stage_key('network', original, cfg | {'stage_versions': versions | {'network': 'network-v2'}})
    assert key != stage_key('network', original, cfg | {'stage_versions': versions | {'curated': 'curate-v2'}})
    assert key == stage_key('network', original, cfg | {'stage_versions': versions | {'scored': 'score-v2'}})
    assert key == stage_key('network', original | {'model_manifest_sha256': 'd' * 64}, cfg)
    assert key not in {stage_key(stage, original, cfg) for stage in ['curated', 'scored']}


def test_deployment_without_network_version_always_recomputes_network():
    s3, cfg, original = prepared()
    older = cfg | {'stage_versions': {'curated': 'curate-v1', 'scored': 'score-v1'}}
    assert stage_key('network', original, older) is None
    assert reuse(s3, 'network', original | {'run_id': 'next-run'}, older, get_json) == {'hit': False, 'reason': 'bypassed'}
    assert not s3.copied


def test_network_branch_skips_glue_only_on_a_cache_hit():
    branch = load_script('infra/ingestion/build-workflow.py').workflow()['States']['ScoreAndAnalyze']['Branches'][2]
    assert branch['StartAt'] == 'ReuseNetwork'
    choice = branch['States']['ReuseNetwork']
    assert choice['Choices'] == [{'Variable': '$.work.network_cache_hit', 'BooleanEquals': True, 'Next': 'NetworkReused'}]
    assert choice['Default'] == 'Network'
    assert branch['States']['NetworkReused'] == {'Type': 'Pass', 'End': True}
    assert 'Retry' not in branch['States']['Network']


def test_parallel_verification_training_dependency_and_bounded_processing_checks():
    workflow = load_script('infra/ingestion/build-workflow.py').workflow()
    states = workflow['States']
    assert states['PrepareBaseline']['Next'] == 'ScoreAndAnalyze'
    branches = states['ScoreAndAnalyze']['Branches']
    baseline = branches[0]['States']
    assert baseline['BaselineComplete']['Choices'][0]['Next'] == 'PrepareBranches'
    assert baseline['PrepareBranches']['Parameters']['op'] == 'baseline'
    assert baseline['PrepareBranches']['Next'] == 'TrainRequested'
    for name, branch_states in [('Baseline', baseline), ('Train', baseline), ('Export', states)]:
        assert 'Retry' not in branch_states[name]
        assert branch_states[name + 'Wait']['Seconds'] == 15
        assert branch_states[name + 'Complete']['Choices'][-1]['NumericGreaterThanEquals'] == 299
    inputs = {item['InputName'] for item in processing_request('test-run', 'export', config(), {'network': 'pipeline/runs/test-run/network/'})['ProcessingInputs']}
    assert inputs == {'run', 'model', 'baseline', 'baseline_manifest', 'scored', 'network'}


def test_cancellation_never_stops_another_run_or_completed_worker():
    stopped = []
    def glue_pages(JobName):
        return [{'JobRuns': [
            {'Id': 'owned', 'JobRunState': 'RUNNING', 'Arguments': {'--output': f's3://{BUCKET}/pipeline/runs/test-run/curated/'}},
            {'Id': 'other', 'JobRunState': 'RUNNING', 'Arguments': {'--output': f's3://{BUCKET}/pipeline/runs/other-run/curated/'}}]}]
    glue = SimpleNamespace(get_paginator=lambda op: SimpleNamespace(paginate=glue_pages),
                           batch_stop_job_run=lambda **kw: stopped.append(kw) or {})
    def sm_pages(NameContains):
        key = 'TrainingJobSummaries' if 'train-' in NameContains else 'ProcessingJobSummaries'
        kind = 'Training' if key.startswith('Training') else 'Processing'
        return [{key: [{kind+'JobName': NameContains, kind+'JobStatus': 'Completed'},
                       {kind+'JobName': NameContains+'-other', kind+'JobStatus': 'InProgress'}]}]
    sm = SimpleNamespace(get_paginator=lambda op: SimpleNamespace(paginate=sm_pages))
    assert stop_owned_workers({'glue': glue, 'sagemaker': sm}, 'test-run') == ['owned']
    assert stopped[0]['JobRunIds'] == ['owned']
