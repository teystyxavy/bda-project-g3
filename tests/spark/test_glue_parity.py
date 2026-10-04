import importlib.util
from pathlib import Path

import pandas as pd
import pytest


pytest.importorskip("pyspark")
from pyspark.sql import SparkSession

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("glue_curation", ROOT / "jobs" / "curate_transactions.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def spark():
    session = SparkSession.builder.master("local[2]").appName("glue-parity").getOrCreate()
    session.conf.set("spark.sql.session.timeZone", "UTC")
    session.conf.set("spark.sql.ansi.enabled", "false")
    session.conf.set("spark.sql.shuffle.partitions", "2")
    yield session
    session.stop()


def test_real_smoke_parity_and_partition_roundtrip(spark, tmp_path):
    sample = ROOT / "output" / "aws-smoke"
    if not (sample / "expected.parquet").exists():
        pytest.fail("Run scripts/prepare-aws-smoke.py before the Spark parity test")
    transactions = MODULE.read_transactions(spark, str(sample / "transactions.csv"))
    accounts = spark.read.option("header", True).csv(str(sample / "accounts.csv"))
    rates = spark.read.option("header", True).csv(str(sample / "fx.csv"))
    result = MODULE.curate(transactions, accounts, rates)
    result.write.partitionBy("txn_date").parquet(str(tmp_path / "curated"))
    actual = spark.read.parquet(str(tmp_path / "curated")).toPandas()
    expected = pd.read_parquet(sample / "expected.parquet").drop(columns=["source_row_index"])
    columns = sorted(expected.columns)
    for frame in [actual, expected]:
        frame["txn_date"] = frame.txn_date.astype(str)
    actual = actual[columns].sort_values(columns).reset_index(drop=True)
    expected = expected[columns].sort_values(columns).reset_index(drop=True)
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False, atol=1e-6, rtol=1e-10)


def test_rejects_duplicate_normalized_accounts_and_missing_prior_fx(spark):
    transactions = spark.createDataFrame([
        ("2022/09/01 00:00", "020", "0007", "20", "0007", "10", "Euro", "10", "Euro", "ACH", "0")
    ], MODULE.TRANSACTION_COLUMNS)
    accounts = spark.createDataFrame([
        ("A", "20", "0007", "E1", "Alice"), ("A", "020", "0007", "E1", "Alice")
    ], list(MODULE.ACCOUNT_COLUMNS))
    rates = spark.createDataFrame([("2022-09-01", "Euro", "1.2")], ["txn_date", "currency", "usd_rate"])
    with pytest.raises(ValueError, match="Duplicate normalized"):
        MODULE.curate(transactions, accounts, rates)
    with pytest.raises(ValueError, match="Missing FX"):
        MODULE.curate(transactions, accounts.limit(1), rates)


def test_non_usd_fx_uses_separate_days_and_preserves_composite_keys(spark):
    transactions = spark.createDataFrame([
        ("2022/09/01 00:00", "020", "0007", "021", "0007", "10", "US Dollar", "10", "Euro", "ACH", "1"),
        ("2022/09/01 01:00", "021", "0007", "021", "0007", "20", "US Dollar", "20", "US Dollar", "Wire", "0"),
    ], MODULE.TRANSACTION_COLUMNS)
    accounts = spark.createDataFrame([
        ("A", "20", "0007", "E1", "Alice"), ("B", "21", "0007", "E2", "Bob")
    ], list(MODULE.ACCOUNT_COLUMNS))
    rates = spark.createDataFrame([
        ("2022-08-31", "Euro", "1.1"), ("2022-09-01", "Euro", "1.2")
    ], ["txn_date", "currency", "usd_rate"])
    actual = MODULE.curate(transactions, accounts, rates).orderBy("timestamp").collect()
    assert [(row.from_bank, row.from_account) for row in actual] == [("020", "0007"), ("021", "0007")]
    assert [row.from_entity_id for row in actual] == ["E1", "E2"]
    assert [row.to_entity_id for row in actual] == ["E2", "E2"]
    assert [row.amount_usd for row in actual] == pytest.approx([12, 20])
    assert [row.amount_usd_asof for row in actual] == pytest.approx([11, 20])
    assert [row.is_self_txn for row in actual] == [False, True]
    assert [row.currency_mismatch for row in actual] == [True, False]
