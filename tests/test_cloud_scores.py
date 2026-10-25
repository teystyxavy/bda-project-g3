import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from bda_pipeline.scoring import cloud_scores as module
from bda_pipeline.scoring.inference import SCORING_COLUMNS
from bda_pipeline.common.integrity import digest


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    frame = pd.DataFrame({name: [0]*12 for name in SCORING_COLUMNS})
    for name in ['from_bank', 'to_bank', 'from_account', 'to_account']:
        frame[name] = '001'
    for name in ['payment_format', 'payment_currency', 'receiving_currency']:
        frame[name] = 'ACH'
    frame['transaction_id'] = [f'HI-Small:{i}' for i in range(12)]
    frame['source_row_index'] = range(12)
    frame['timestamp'] = pd.Timestamp('2022-09-07')
    frame['amount_usd'] = np.arange(12, dtype='float64')
    reference = tmp_path/'reference.parquet'
    frame.to_parquet(reference, index=False)
    models = tmp_path/'models'; models.mkdir()
    (models/'model-manifest.json').write_text('{}')
    output = tmp_path/'cloud'; output.mkdir()
    cloud = frame.assign(model_score=frame.amount_usd/100, txn_date='2022-09-07').iloc[::-1]
    cloud.to_parquet(output/'part.parquet', index=False)
    reports = dict(
        run={'JobRun': {'Id': 'full-run', 'JobName': 'is459-g3-score-frozen', 'JobRunState': 'SUCCEEDED'}},
        job={'Job': {'Name': 'is459-g3-score-frozen', 'NonOverridableArguments': {
            '--INPUT': 's3://bucket/curated/full/transactions/', '--OUTPUT': 's3://bucket/scored/full/transactions/',
            '--EXPECTED_ROWS': '12', '--MANIFEST_SHA256': digest(models/'model-manifest.json'),
            '--conf': 'spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs=true'}}},
        marker={'job_run_id': 'full-run', 'bucket': 'bucket', 'key': 'scored/full/transactions/_SUCCESS',
                'metadata': {'ContentLength': 0, 'ServerSideEncryption': 'AES256'}},
    )
    for name, content in reports.items():
        (tmp_path/f'{name}.json').write_text(json.dumps(content))
    ranker = SimpleNamespace(selection={'source_sha256': digest(reference), 'selected': {'sha256': 'a'*64}},
                            report={'total_rows': 12}, predict=lambda f: f.amount_usd.to_numpy()/100)
    monkeypatch.setattr(module, 'SavedRanker', lambda path: ranker)
    monkeypatch.setattr(module, 'verify_artifacts', lambda path, sha: {})
    def verify(destination='verified'):
        return module.verify_full_scores(output, reference, models, tmp_path/'run.json',
                        tmp_path/'job.json', tmp_path/'marker.json', tmp_path/destination, 12, 5)
    return tmp_path, cloud, verify


def test_all_rows_reordered_across_batches_and_cache_integrity(cohort):
    root, frame, verify = cohort
    report = verify()
    values, metadata = module.load_verified_scores(root/'verified/verification.json',
                       report['source_sha256'], 'a'*64, 12)
    np.testing.assert_array_equal(values, np.arange(12)/100)
    assert metadata['max_absolute_score_difference'] == 0
    assert metadata['labels_excluded'] and metadata['success_marker_verified']
    with pytest.raises(ValueError, match='mismatch'):
        module.load_verified_scores(root/'verified/verification.json', 'wrong', 'a'*64, 12)
    with (root/'verified/scores-by-source-index.npy').open('ab') as stream:
        stream.write(b'tamper')
    with pytest.raises(ValueError, match='hash'):
        module.load_verified_scores(root/'verified/verification.json', report['source_sha256'], 'a'*64, 12)


@pytest.mark.parametrize('change,error', [
    ('duplicate', 'Duplicate'), ('missing', 'Incomplete'), ('feature', 'fingerprint'),
    ('score', 'Invalid cloud score'), ('label', 'evaluation labels'), ('date', 'partition'),
    ('wrong_marker', 'receipt mismatch'), ('failed', 'successful'),
    ('prediction', 'Not equal'), ('marker_size', 'success marker'),
    ('wrong_population', 'receipt mismatch'),
])
def test_reject_partial_tampered_or_uncommitted_output(cohort, change, error):
    root, frame, verify = cohort
    if change == 'duplicate': frame = pd.concat([frame, frame.iloc[:1]])
    if change == 'missing': frame = frame.iloc[1:]
    if change == 'feature': frame = frame.assign(from_bank='1')
    if change == 'score': frame = frame.assign(model_score=np.nan)
    if change == 'prediction': frame = frame.assign(model_score=0.25)
    if change == 'label': frame = frame.assign(is_laundering=0)
    if change == 'date': frame = frame.assign(txn_date='2022-09-08')
    if change == 'wrong_marker':
        path=root/'marker.json'; receipt=json.loads(path.read_text()); receipt['job_run_id']='smoke'
        path.write_text(json.dumps(receipt))
    if change == 'failed':
        path=root/'run.json'; run=json.loads(path.read_text()); run['JobRun']['JobRunState']='FAILED'
        path.write_text(json.dumps(run))
    if change == 'marker_size':
        path=root/'marker.json'; receipt=json.loads(path.read_text()); receipt['metadata']['ContentLength']=1
        path.write_text(json.dumps(receipt))
    if change == 'wrong_population':
        path=root/'job.json'; job=json.loads(path.read_text())
        job['Job']['NonOverridableArguments']['--EXPECTED_ROWS']='1024'
        path.write_text(json.dumps(job))
    frame.to_parquet(root/'cloud/part.parquet', index=False)
    with pytest.raises((ValueError, AssertionError), match=error):
        verify()
    assert not (root/'verified').exists()
