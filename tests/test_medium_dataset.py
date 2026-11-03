"""Medium contracts, cross-subset isolation, and offline launcher checks."""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bda_pipeline.cloud_scores import checked_indices
from bda_pipeline.ingest_contract import stage_request, sha, validate_manifest
from bda_pipeline.stage_cache import stage_key
from test_ingestion import config, manifest

ROOT = Path(__file__).resolve().parents[1]


def medium_manifest(**updates):
    value = manifest() | {'subset': 'HI-Medium', 'rows': 31_898_238}
    value['files'] = {k: dict(v) for k, v in value['files'].items()}
    value['files']['transactions.csv']['bytes'] = 3_100_000_000
    value.update(updates)
    value['processing_sha256'] = sha({key: value[key] for key in [
        'subset', 'rows', 'files', 'start_date', 'end_date', 'dataset_version',
        'deployment_sha256', 'train']})
    return value


def test_medium_size_bounds_and_cache_isolation():
    value = medium_manifest()
    validate_manifest(value, sha(config()))
    # Any size, subset, training flag and end date is accepted; only malformed values are rejected.
    for updates in [{'rows': 35_000_001}, {'train': True}, {'subset': 'HI-Large'}, {'subset': 'LI-Medium'},
                    {'end_date': '2022-10-01'}]:
        validate_manifest(medium_manifest(**updates), sha(config()))
    for updates in [{'rows': 0}, {'rows': True}, {'subset': 'HI-Huge'}, {'subset': 'HI-Small/../x'},
                    {'start_date': '2022-09-30', 'end_date': '2022-09-01'}]:
        with pytest.raises(ValueError):
            validate_manifest(medium_manifest(**updates), sha(config()))
    cfg = config() | {'stage_versions': {'curated': 'a', 'scored': 'b'}}
    small = value | {'subset': 'HI-Small'}
    for stage in ['curated', 'scored']:
        assert stage_key(stage, small, cfg) != stage_key(stage, value, cfg)


def test_subset_identity_cannot_be_mixed():
    frame = pd.DataFrame({'source_row_index': [0, 1],
                          'transaction_id': ['HI-Medium:0', 'HI-Medium:1']})
    np.testing.assert_array_equal(checked_indices(frame, np.zeros(2, bool), 'HI-Medium'), [0, 1])
    with pytest.raises(ValueError, match='identity'):
        checked_indices(frame, np.zeros(2, bool), 'HI-Small')
    frame.loc[1, 'transaction_id'] = 'HI-Small:1'
    with pytest.raises(ValueError, match='identity'):
        checked_indices(frame, np.zeros(2, bool), 'HI-Medium')


def test_medium_processing_is_bounded_and_small_settings_preserved():
    for stage in ['baseline', 'export']:
        small = stage_request('test-run', stage, config())
        medium = stage_request('test-run', stage, config(), subset='HI-Medium')
        assert small['WorkerType'] == medium['WorkerType'] == 'G.2X'
        assert small['NumberOfWorkers'] == medium['NumberOfWorkers'] == 2
        assert small['Timeout'] == 30
        assert medium['Timeout'] == 60
        large = stage_request('test-run', stage, config(), subset='LI-Large', rows=179_702_229)
        assert large['Timeout'] == 360
        assert large['WorkerType'] == 'G.2X'



def test_run_profiles_are_chosen_by_row_count_with_no_ceiling():
    from bda_pipeline.ingest_contract import run_profile
    rows = [1, 5_078_345, 10_000_000, 10_000_001, 31_898_238, 50_000_000, 50_000_001, 2_000_000_000]
    assert [run_profile(count)['name'] for count in rows] == [
        'small', 'small', 'small', 'medium', 'medium', 'medium', 'large', 'large']
    for bad in [0, -1, True, 1.5, None]:
        with pytest.raises(ValueError):
            run_profile(bad)


def test_medium_preview_uses_local_route_without_network(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location('medium_launcher', ROOT / 'scripts/ingestion/ingest.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, 'aws_clients', lambda *a: pytest.fail('AWS called during preview'))
    monkeypatch.setattr(sys, 'argv', ['ingest', 'run', '--subset', 'HI-Medium',
                                    '--local-acquisition', '--dry-run'])
    cli.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan['subset'] == 'HI-Medium'
    assert plan['acquisition_location'] == 'local'
    assert plan['files'] == ['HI-Medium_Trans.csv', 'HI-Medium_accounts.csv']


def test_medium_tail_includes_final_source_day():
    from bda_pipeline.batch import evaluation_periods
    from bda_pipeline.splits import LEGACY_SPLITS
    assert evaluation_periods(LEGACY_SPLITS, '2022-09-28')['tail'] == ('2022-09-11', '2022-09-29')
    assert evaluation_periods(LEGACY_SPLITS, '2022-09-18')['tail'] == ('2022-09-11', '2022-09-19')


def test_streamed_ranking_matches_full_population_with_cross_batch_ties():
    from bda_pipeline.dashboard_exploration import RankedPopulation, scorer_projection
    frame = pd.DataFrame({'timestamp': pd.to_datetime(['2022-09-07'] * 321),
        'source_row_index': np.arange(321), 'is_laundering': (np.arange(321) % 13 == 0).astype(int),
        'transaction_id': ['HI-Medium:' + str(i) for i in range(321)]})
    scores = (np.arange(321) % 17) / 16
    expected = scorer_projection(frame, scores, 250, subset='HI-Medium')
    population = RankedPopulation(250, True, 'HI-Medium')
    for indices in [np.arange(120, 260), np.arange(120), np.arange(260, 321)]:
        population.add(frame.iloc[indices], scores[indices])
    assert population.finish() == expected
    assert population.queue.transaction_id.tolist() == expected['queue']


def test_retention_only_forgets_predecessor_immutable_uploads():
    spec = importlib.util.spec_from_file_location('retain_artifacts', ROOT / 'scripts/aws/retain-deployment-artifacts.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    prefix = 'pipeline/deployments/' + 'a' * 64
    previous = 'pipeline/deployments/' + 'b' * 64
    instance = {'index_key': 'control.zip', 'attributes': {
        'bucket': helper.BUCKET, 'key': previous + '/control.zip'}}
    state = {'resources': [{'type': 'aws_s3_object', 'name': 'deployment_artifact',
                           'mode': 'managed', 'instances': [instance]}]}
    assert helper.predecessor_addresses(state, prefix) == ['aws_s3_object.deployment_artifact["control.zip"]']
    assert helper.predecessor_addresses(state, previous) == []
    instance['attributes']['key'] = 'pipeline/runs/some-run/raw/control.zip'
    with pytest.raises(ValueError, match='outside'):
        helper.predecessor_addresses(state, prefix)


def test_workflow_glue_overrides_include_worker_type():
    spec = importlib.util.spec_from_file_location('medium_workflow', ROOT / 'infra/ingestion/build-workflow.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    states = module.workflow()['States']
    tasks = [states['Curate'], states['ScoreAndAnalyze']['Branches'][1]['States']['Score']]
    for task in tasks:
        assert task['Parameters']['WorkerType'] == 'G.1X'
        assert task['Parameters']['NumberOfWorkers.$'] == '$.work.glue_workers'


def test_large_stage_receipt_is_bounded_without_truncation():
    from bda_pipeline.ingest_control import get_json, BUCKET
    from test_ingestion import MemoryS3
    client = MemoryS3()
    receipt = {'records': ['x' * 1024] * 256}
    client.put_object(Bucket=BUCKET, Key='pipeline/stage-cache/test.json', Body=json.dumps(receipt).encode())
    assert get_json(client, 'pipeline/stage-cache/test.json')[0] == receipt
    client.put_object(Bucket=BUCKET, Key='pipeline/stage-cache/test.json', Body=b'x' * 1_000_001)
    with pytest.raises(ValueError, match='too large'):
        get_json(client, 'pipeline/stage-cache/test.json')
