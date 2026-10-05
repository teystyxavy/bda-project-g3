import numpy as np
import pytest

from bda_pipeline.inference import SCORING_COLUMNS, score_frame, verify_artifacts


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


def test_manifest_external_anchor(tmp_path):
    (tmp_path / 'model-manifest.json').write_text('{}')
    with pytest.raises(ValueError, match='Trusted'):
        verify_artifacts(tmp_path, '0'*64)
