import numpy as np
import pytest

from bda_pipeline.inference import SCORING_COLUMNS, resolve_model_directory, score_frame, verify_artifacts


def test_label_free_scoring_and_fail_closed_inputs():
    import pandas as pd
    from bda_pipeline.batch import FEATURES
    from bda_pipeline.model import CATEGORICAL
    frame = pd.DataFrame(dict(transaction_id=['HI-Small:0'], source_row_index=[0],
        timestamp=pd.to_datetime(['2022-09-01']), from_bank=['001'], from_account=['000A'],
        to_bank=['1'], to_account=['000B'], amount_usd_asof=[1.], amount_usd=[2.],
        **{name: [0] for name in FEATURES}, **{name: ['test'] for name in CATEGORICAL}))
    class Ranker:
        def predict(self, batch):
            assert 'is_laundering' not in batch
            return np.array([.4]*len(batch))
    result = score_frame(frame.assign(is_laundering=1), Ranker())
    assert result.columns.tolist() == SCORING_COLUMNS + ['model_score']
    assert result.from_account.iloc[0] == '000A'
    for changed in [frame.assign(from_bank=1), frame.assign(amount_usd=np.inf),
                    frame.assign(timestamp=None), frame.assign(source_row_index=-1),
                    frame.drop(columns='txn_count_1h')]:
        with pytest.raises(ValueError):
            score_frame(changed, Ranker())
    with pytest.raises(ValueError, match='duplicate'):
        score_frame(pd.concat([frame, frame]), Ranker())



def test_feature_subset_must_cover_the_frozen_model():
    import pandas as pd
    from bda_pipeline.batch import FEATURES
    from bda_pipeline.model import CATEGORICAL
    subset = [name for name in FEATURES if 'amount_sum' not in name and name != 'corridor_prior_count']
    frame = pd.DataFrame(dict(transaction_id=['HI-Small:0'], source_row_index=[0],
        timestamp=pd.to_datetime(['2022-09-01']), from_bank=['001'], from_account=['000A'],
        to_bank=['1'], to_account=['000B'], amount_usd_asof=[1.], amount_usd=[2.],
        **{name: [0] for name in subset}, **{name: ['test'] for name in CATEGORICAL}))
    class Ranker:
        feature_names = ['log1p_amount_usd_asof', *('log1p_' + name for name in subset), 'is_self_txn']
        def predict(self, batch):
            return np.array([.4]*len(batch))
    result = score_frame(frame, Ranker(), subset)
    assert result.columns.tolist() == [c for c in SCORING_COLUMNS if c not in FEATURES or c in subset] + ['model_score']
    # The batch default still requires every batch feature.
    with pytest.raises(ValueError, match='Missing scoring columns'):
        score_frame(frame, Ranker())
    # A subset that misses a feature the model reads is refused.
    with pytest.raises(ValueError, match='do not cover'):
        score_frame(frame, Ranker(), subset[:5])


def test_manifest_external_anchor(tmp_path):
    (tmp_path / 'model-manifest.json').write_text('{}')
    with pytest.raises(ValueError, match='Trusted'):
        verify_artifacts(tmp_path, '0'*64)


def test_default_directory_uses_localized_manifest_instead_of_cwd(tmp_path, monkeypatch):
    localized = tmp_path / 'localized'
    localized.mkdir()
    manifest = localized / 'model-manifest.json'
    manifest.write_text('{}')
    working = tmp_path / 'working'
    working.mkdir()
    (working / 'model-manifest.json').write_text('wrong working-directory file')
    monkeypatch.chdir(working)
    assert resolve_model_directory('.', manifest) == localized.resolve()
    with pytest.raises(ValueError, match='localized model manifest is missing'):
        resolve_model_directory('.', tmp_path / 'absent.json')
    assert resolve_model_directory(localized, tmp_path / 'absent.json') == localized.resolve()


def test_manifest_diagnostics_distinguish_symlink_and_changed_bytes(tmp_path):
    import json
    from bda_pipeline.model import digest
    names = ['comparison.json', 'features.json', 'selection.json', 'hist-depth5.ubj']
    for name in names:
        (tmp_path / name).write_bytes(b'original')
    manifest = dict(model_file='hist-depth5.ubj', files={n: digest(tmp_path / n) for n in names})
    anchor = tmp_path / 'model-manifest.json'
    anchor.write_text(json.dumps(manifest))
    trusted = digest(anchor)
    (tmp_path / 'comparison.json').write_bytes(b'changed')
    with pytest.raises(ValueError, match='expected=.*actual='):
        verify_artifacts(tmp_path, trusted)


def test_symlink_rejection_is_explicit(tmp_path):
    import json
    from bda_pipeline.model import digest
    names = ['comparison.json', 'features.json', 'selection.json', 'hist-depth5.ubj']
    for name in names:
        (tmp_path / name).write_bytes(b'original')
    manifest = dict(model_file='hist-depth5.ubj', files={n: digest(tmp_path / n) for n in names})
    anchor = tmp_path / 'model-manifest.json'
    anchor.write_text(json.dumps(manifest))
    trusted = digest(anchor)
    original = tmp_path / 'comparison-original.json'
    (tmp_path / 'comparison.json').rename(original)
    try:
        (tmp_path / 'comparison.json').symlink_to(original)
    except OSError:
        pytest.skip('Symlinks unavailable; covered in the Glue Linux container')
    with pytest.raises(ValueError, match='must not be a symlink'):
        verify_artifacts(tmp_path, trusted)
