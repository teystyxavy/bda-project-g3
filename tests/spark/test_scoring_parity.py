import importlib.util
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip('pyspark')
pytest.importorskip('xgboost')
from pyspark.sql import SparkSession
from bda_pipeline.inference import score_frame, worker_ranker

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(os.environ.get('CLOUD_SCORING_PACKAGE', ROOT / 'output/cloud-scoring-verified-20261005'))
SPEC = importlib.util.spec_from_file_location('glue_scoring', ROOT / 'jobs/score_transactions.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_saved_model_spark_arrow_and_partition_roundtrip(tmp_path):
    if not (PACKAGE / 'package.json').exists():
        pytest.fail('Prepare the cloud-scoring parity package first')
    metadata = json.loads((PACKAGE / 'package.json').read_text())
    ranker = worker_ranker(str(PACKAGE / 'artifacts'), metadata['manifest_sha256'])
    expected = pd.read_parquet(PACKAGE / 'fixture/expected.parquet')
    native_input = pd.read_parquet(PACKAGE / 'fixture/input.parquet')
    direct = score_frame(native_input, ranker)
    np.testing.assert_allclose(direct.model_score, expected.model_score, rtol=1e-6, atol=1e-7)
    # Unknown categories map to the saved unknown feature, not a new vocabulary.
    changed = native_input.iloc[:1].assign(payment_format='UnseenFormat')
    assert np.isfinite(score_frame(changed, ranker).model_score).all()
    session = SparkSession.builder.master('local[2]').appName('frozen-score-parity').getOrCreate()
    session.conf.set('spark.sql.shuffle.partitions', '2')
    try:
        session.sparkContext.addPyFile(str(PACKAGE / 'bda_pipeline.zip'))
        report = MODULE.score(session, str(PACKAGE / 'fixture/input.parquet'),
                              str(tmp_path / 'scored'), str(PACKAGE / 'artifacts'),
                              metadata['manifest_sha256'], len(expected))
        actual = session.read.parquet(str(tmp_path / 'scored')).toPandas()
        actual = actual.sort_values('source_row_index').reset_index(drop=True)
        expected = expected.sort_values('source_row_index').reset_index(drop=True)
        assert report['rows'] == len(expected)
        assert 'is_laundering' not in actual
        pd.testing.assert_frame_equal(actual[expected.columns].drop(columns='model_score'),
            expected.drop(columns='model_score'), check_dtype=False)
        np.testing.assert_allclose(actual.model_score, expected.model_score, rtol=1e-6, atol=1e-7)
        assert (actual.txn_date.astype(str) == actual.timestamp.dt.strftime('%Y-%m-%d')).all()
        queue = ranker.queue(actual, actual.model_score.to_numpy())
        expected_queue = pd.read_parquet(PACKAGE / 'fixture/expected-queue.parquet')
        assert queue.transaction_id.tolist() == expected_queue.transaction_id.tolist()
        assert queue.model_evidence.tolist() == expected_queue.model_evidence.tolist()
        np.testing.assert_allclose(queue.explanation_total_margin, expected_queue.explanation_total_margin,
                                   rtol=1e-6, atol=1e-6)
        # Glue has already registered --extra-files in Spark. The default must
        # read those copies without re-registering a conflicting source path.
        MODULE.score(session, str(PACKAGE / 'fixture/input.parquet'),
                     str(tmp_path / 'localized-scored'), '.', metadata['manifest_sha256'], len(expected))
        localized = session.read.parquet(str(tmp_path / 'localized-scored')).toPandas()
        localized = localized.sort_values('source_row_index').reset_index(drop=True)
        np.testing.assert_allclose(localized.model_score, expected.model_score, rtol=1e-6, atol=1e-7)
        with pytest.raises(ValueError, match='fresh'):
            MODULE.score(session, str(PACKAGE / 'fixture/input.parquet'), str(tmp_path / 'scored'),
                         str(PACKAGE / 'artifacts'), metadata['manifest_sha256'], len(expected))
        with pytest.raises(ValueError, match='identity'):
            MODULE.score(session, str(PACKAGE / 'fixture/input.parquet'), str(tmp_path / 'bad'),
                         str(PACKAGE / 'artifacts'), metadata['manifest_sha256'], len(expected)+1)
    finally:
        session.stop()
