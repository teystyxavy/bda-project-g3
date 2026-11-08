import json

import numpy as np
import pandas as pd
import pytest

from bda_pipeline.network import analyse_network, candidate_loops
from bda_pipeline.network_checkpoint import NetworkCheckpoint
from bda_pipeline.ingest_contract import BUCKET
from network_fixture import factory, network_frame
from test_ingestion import MemoryS3


def test_chunked_triangles_match_directed_graph_oracle():
    rng = np.random.default_rng(459)
    for size in (0, 3, 20, 70):
        nodes = [f'1:{i:03}' for i in range(size)]
        edges = {(a, b) for a in nodes for b in nodes if a != b and rng.random() < .15}
        frame = pd.DataFrame(sorted(edges), columns=['src', 'dst'])
        expected = {(a, b, c) for a, b in edges for c in nodes
                    if a < b and a < c and (b, c) in edges and (c, a) in edges}
        for chunk_size in (1, 7, 20_000):
            assert set(candidate_loops(frame, chunk_size).itertuples(index=False, name=None)) == expected


def test_network_checkpoints_restore_same_results_and_index(tmp_path):
    s3 = MemoryS3()
    checkpoint = NetworkCheckpoint(s3, 'test-run', {'source': 'fixture'}, tmp_path)
    expected = analyse_network(factory(network_frame()), checkpoint)
    def no_read():
        raise AssertionError('Completed checkpoint should avoid another data scan')
    restored = analyse_network(no_read, checkpoint)
    for name in expected:
        pd.testing.assert_frame_equal(expected[name], restored[name])
    # Link checkpoint is independently useful after a later network failure.
    del s3.data[BUCKET, checkpoint.prefix + 'result/manifest.json']
    restarted = analyse_network(factory(network_frame()), checkpoint)
    for name in expected:
        pd.testing.assert_frame_equal(expected[name], restarted[name])


def test_checkpoint_partial_upload_and_corruption(tmp_path):
    s3 = MemoryS3()
    checkpoint = NetworkCheckpoint(s3, 'test-run', {'source': 'fixture'}, tmp_path)
    checkpoint.save('links', {'links': pd.DataFrame({'src': ['a']}), 'counts': pd.DataFrame({'rows': [1]})})
    marker = checkpoint.prefix + 'links/manifest.json'
    commit = s3.data.pop((BUCKET, marker))
    assert checkpoint.load('links') is None
    s3.data[BUCKET, marker] = commit
    s3.data[BUCKET, checkpoint.prefix + 'links/links.parquet'] = b'corrupt'
    with pytest.raises(ValueError, match='checksum'):
        checkpoint.load('links')
    data = json.loads(commit)
    data['files']['../escape'] = 'bad'
    s3.data[BUCKET, marker] = json.dumps(data).encode()
    with pytest.raises(ValueError, match='table mismatch'):
        checkpoint.load('links')


def test_concurrent_checkpoint_reader_never_writes(tmp_path):
    from bda_pipeline.network_checkpoint import ReadOnlyNetworkCheckpoint
    s3 = MemoryS3()
    writer = NetworkCheckpoint(s3, 'test-run', {'source': 'fixture'}, tmp_path / 'writer')
    expected = analyse_network(factory(network_frame()), writer)
    before = dict(s3.data)
    reader = ReadOnlyNetworkCheckpoint(s3, 'test-run', {'source': 'fixture'}, tmp_path / 'reader')
    restored = analyse_network(lambda: (_ for _ in ()).throw(AssertionError('Unexpected scan')), reader)
    reader.save('result', restored)
    assert s3.data == before
    for name in expected:
        pd.testing.assert_frame_equal(expected[name], restored[name])


def test_legacy_checkpoint_migration_is_exact_and_source_bound(tmp_path):
    s3 = MemoryS3()
    old_identity = dict(reference_sha256='ref', manifest_sha256='manifest', network_code_sha256='old')
    old = NetworkCheckpoint(s3, 'test-run', old_identity, tmp_path / 'old')
    expected = analyse_network(factory(network_frame()), old)
    new_identity = dict(old_identity, network_code_sha256='new')
    current = NetworkCheckpoint(s3, 'test-run', new_identity, tmp_path / 'new', compatible_network_hashes=('old',))
    def no_scan():
        raise AssertionError('Legacy complete checkpoint should avoid recalculation')
    actual = analyse_network(no_scan, current)
    for name in expected:
        pd.testing.assert_frame_equal(expected[name], actual[name])
    changed_source = NetworkCheckpoint(s3, 'test-run', dict(new_identity, reference_sha256='other'),
                                     tmp_path / 'other', compatible_network_hashes=('old',))
    assert changed_source.load('result') is None
    strict = NetworkCheckpoint(s3, 'test-run', new_identity, tmp_path / 'strict')
    assert strict.load('result') is None


def test_completed_payment_scan_checkpoint_avoids_second_input_pass(tmp_path):
    s3 = MemoryS3()
    cp = NetworkCheckpoint(s3, 'test-run', {'source': 'fixture'}, tmp_path)
    expected = analyse_network(factory(network_frame()), cp)
    del s3.data[BUCKET, cp.prefix + 'result/manifest.json']
    def no_scan():
        raise AssertionError('Saved links and payments should avoid both scans')
    actual = analyse_network(no_scan, cp)
    for name in expected:
        pd.testing.assert_frame_equal(expected[name], actual[name])
