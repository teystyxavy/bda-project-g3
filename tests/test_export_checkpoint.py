import json
import io

import pytest

from bda_pipeline.export_checkpoint import ExportCheckpoint
from bda_pipeline.ingest_contract import BUCKET
from test_ingestion import MemoryS3, staged_run, baseline_stage, serve_inputs, ROOT
from bda_pipeline.ingest_compute import export_stage
from bda_pipeline.network_checkpoint import NetworkCheckpoint


class BinaryS3(MemoryS3):
    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(self.data[Bucket, Key])}


def test_substage_commits_last_restores_and_rejects_corruption():
    s3 = BinaryS3()
    cache = ExportCheckpoint(s3, 'test-run', {'source': 'a', 'code': 'v1'})
    assert cache.compute('view-test', lambda: {'rows': 123, 'ap': .5}) == {'rows': 123, 'ap': .5}
    def no_compute():
        raise AssertionError('Unexpected repeat calculation')
    assert cache.compute('view-test', no_compute)['rows'] == 123
    payload = cache.key('view-test') + 'payload.json.gz'
    s3.data[BUCKET, payload] = b'corrupt'
    with pytest.raises(ValueError, match='checksum'):
        cache.compute('view-test', no_compute)
    # An orphan payload without commit metadata is never reused.
    del s3.data[BUCKET, cache.key('view-test') + 'manifest.json']
    assert cache.compute('view-test', lambda: {'rows': 456})['rows'] == 456
    different = ExportCheckpoint(s3, 'test-run', {'source': 'a', 'code': 'v2'})
    assert different.prefix != cache.prefix
    assert different.compute('view-test', lambda: {'rows': 789})['rows'] == 789
    with pytest.raises(ValueError, match='name'):
        cache.compute('../escape', no_compute)


def test_export_restarts_reuse_completed_substages_but_reverify_scores(tmp_path, monkeypatch):
    inputs, value = staged_run(tmp_path, 'HI-Small')
    baseline = inputs / 'baseline'
    baseline_stage(inputs, baseline, ROOT / 'dashboard')
    _, cloud = serve_inputs(tmp_path, inputs, baseline, value)
    s3 = BinaryS3()
    factory = lambda identity: NetworkCheckpoint(s3, value['run_id'], identity, tmp_path / 'cp')
    export_stage(inputs, tmp_path / 'first-export', ROOT / 'dashboard', checkpoint_factory=factory, component_review=True)
    first = json.loads((tmp_path / 'first-export/dashboard/dashboard.json').read_text())
    def no_repeat(*args, **kwargs):
        raise AssertionError('Repeated completed substage')
    monkeypatch.setattr('bda_pipeline.dashboard_exploration.stream_model_view', no_repeat)
    monkeypatch.setattr('bda_pipeline.dashboard_exploration.enrich_snapshot', no_repeat)
    monkeypatch.setattr('bda_pipeline.ingest_compute.dashboard_section', no_repeat)
    export_stage(inputs, tmp_path / 'second-export', ROOT / 'dashboard', checkpoint_factory=factory, component_review=True)
    second = json.loads((tmp_path / 'second-export/dashboard/dashboard.json').read_text())
    assert first['model'] == second['model'] and first['network'] == second['network']
    assert first['exploration'] == second['exploration']
    # Cached views must never hide tampered scoring inputs.
    cloud.loc[0, 'model_score'] = .987654321
    cloud.to_parquet(inputs / 'scored/data.parquet', index=False)
    with pytest.raises(AssertionError):
        export_stage(inputs, tmp_path / 'bad-export', ROOT / 'dashboard', checkpoint_factory=factory, component_review=True)
