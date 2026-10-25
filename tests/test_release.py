import json

import pytest

from bda_pipeline.control.release import ASSETS, prepare_release


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
