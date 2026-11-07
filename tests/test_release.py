import json

import pytest

from bda_pipeline.release import ASSETS, prepare_release


def fixture(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    snapshot = dict(buildStatus='complete', aml_scope='full',
                    provenance={'curated': 'C:\\Users\\private\\data', 'verification_sha256': 'abc'},
                    model={'provenance': {'model_sha256': 'abc'},
                           'views': {'test': {'queue': [{'from_account': '00123'}]}}},
                    treasury={'total_amount_usd': 123}, aml={'test': {'metrics': {'positives': 2}}})
    for name in ASSETS:
        (source / name).write_text(json.dumps(snapshot) if name.endswith('.json') else 'asset', encoding='utf-8')
    (source / 'secret.tfstate').write_text('must not ship')
    return source, snapshot


def test_date_subset_snapshot_is_never_released(tmp_path):
    # A date-subset baseline export (see batch.export_verified_batch) is a training-candidate
    # exploration, never a publishable release - this locks that in as a tested contract.
    source, _ = fixture(tmp_path)
    for name in ASSETS:
        if name.endswith('.json'):
            data = json.loads((source / name).read_text())
            data['aml_scope'] = 'date-subset'
            (source / name).write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        prepare_release(source, tmp_path / 'release')


def test_allowlist_and_preservation(tmp_path):
    source, original = fixture(tmp_path)
    manifest = prepare_release(source, tmp_path / 'release')
    site = tmp_path / 'release' / 'site'
    assert {p.name for p in site.iterdir()} == set(ASSETS)
    exported = json.loads((site / 'dashboard.json').read_text())
    assert 'curated' not in exported['provenance']
    assert exported['treasury'] == original['treasury']
    assert exported['aml'] == original['aml']
    assert exported['model']['views']['test']['queue'][0]['from_account'] == '00123'
    assert json.loads((source / 'dashboard.json').read_text()) == original
    assert not manifest['public_demo_approved']
    assert manifest['total_bytes'] == sum(p.stat().st_size for p in site.iterdir())
    with pytest.raises(ValueError, match='fresh'):
        prepare_release(source, tmp_path / 'release')


@pytest.mark.parametrize('field', ['is_laundering', 'attempt_id', 'pattern_type', 'aws_secret_access_key'])
def test_reject_private_fields_before_writing(tmp_path, field):
    source, snapshot = fixture(tmp_path)
    snapshot['model']['views']['test']['queue'][0][field] = 'private'
    (source / 'dashboard.json').write_text(json.dumps(snapshot))
    with pytest.raises(ValueError, match='Forbidden'):
        prepare_release(source, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


def test_reject_private_path_in_other_assets(tmp_path):
    source, _ = fixture(tmp_path)
    (source / 'dashboard.js').write_text('C:\\Users\\private\\secret')
    with pytest.raises(ValueError, match='private path'):
        prepare_release(source, tmp_path / 'release')


def test_medium_snapshot_above_old_demo_limit_preserves_content(tmp_path):
    source, snapshot = fixture(tmp_path)
    snapshot['exploration'] = {'description': 'x' * 10_000_000}
    (source / 'dashboard.json').write_text(json.dumps(snapshot))
    manifest = prepare_release(source, tmp_path / 'release')
    exported = json.loads((tmp_path / 'release/site/dashboard.json').read_bytes())
    assert exported['exploration'] == snapshot['exploration']
    assert exported['model'] == snapshot['model']
    assert manifest['total_bytes'] > 10_000_000


def test_oversized_snapshot_reports_size_and_limit(tmp_path, monkeypatch):
    from bda_pipeline import release
    source, _ = fixture(tmp_path)
    size = (source / 'dashboard.json').stat().st_size
    monkeypatch.setitem(release.ASSET_MAX_BYTES, 'dashboard.json', size - 1)
    with pytest.raises(ValueError, match=rf'dashboard.json \({size} bytes; limit {size - 1}\)'):
        prepare_release(source, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()


def test_total_release_budget_still_enforced(tmp_path, monkeypatch):
    from bda_pipeline import release
    source, _ = fixture(tmp_path)
    monkeypatch.setattr(release, 'RELEASE_MAX_BYTES', 1)
    with pytest.raises(ValueError, match='package guard'):
        prepare_release(source, tmp_path / 'release')
    assert not (tmp_path / 'release').exists()
