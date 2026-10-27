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
        pytest.fail("Run scripts/validation/prepare-aws-smoke.py before the Spark parity test")
    transactions = MODULE.read_transactions(spark, str(sample / "transactions.csv"))
    indexed = transactions.orderBy("source_row_index").collect()
    assert [row.source_row_index for row in indexed] == list(range(1000))
    assert [row.transaction_id for row in indexed] == [f"HI-Small:{i}" for i in range(1000)]
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


def test_exact_behaviour_matches_local_boundaries_peers_and_banks(spark):
    from bda_pipeline.analytics import add_behaviour

    times = ["2022-09-01 00:00", "2022-09-01 00:00", "2022-09-01 00:01",
             "2022-09-01 01:00", "2022-09-02 00:00", "2022-09-02 00:01",
             "2022-09-08 00:00", "2022-09-08 00:01", "2022-09-09 00:00"]
    rows = [(i, f"HI-Small:{i}", pd.Timestamp(t).to_pydatetime(),
             "020" if i % 2 else "20", "0001", "021" if i % 2 else "21", "0002")
            for i, t in enumerate(times)]
    rows += [(9, "HI-Small:9", pd.Timestamp(times[0]).to_pydatetime(), "99", "0001", "21", "0002"),
             (10, "HI-Small:10", pd.Timestamp(times[2]).to_pydatetime(), "20", "0001", "22", "0002")]
    columns = ["source_row_index", "transaction_id", "timestamp", "from_bank", "from_account", "to_bank", "to_account"]
    frame = spark.createDataFrame(rows, columns)
    expected = add_behaviour(pd.DataFrame(rows, columns=columns)).sort_values("source_row_index").reset_index(drop=True)
    actual = MODULE.add_behaviour(frame.repartition(3)).orderBy("source_row_index").toPandas()
    pd.testing.assert_frame_equal(actual[expected.columns], expected, check_dtype=False)
    assert actual.loc[2, "txn_count_1h"] == 2
    assert actual.loc[9, "txn_count_7d"] == 0
    # Row 9 is a third, distinct sender ("99") paying the same receiver as
    # rows 0-8, at the exact same timestamp as row 0 -- so both are excluded
    # from each other (same-timestamp rule) but both count for row 2, the
    # next transaction to that receiver: fan-in from two distinct senders.
    assert actual.loc[2, "distinct_senders_7d"] == 2


def test_source_ids_preserve_duplicate_records(spark, tmp_path):
    import csv
    path = tmp_path / "transactions.csv"
    row = ["2022/09/01 00:00", "020", "0007", "20", "0007", "10", "Euro", "10", "Euro", "ACH", "0"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(MODULE.RAW_HEADER)
        writer.writerows([row, row])
    first = MODULE.read_transactions(spark, str(path)).orderBy("source_row_index").collect()
    second = MODULE.read_transactions(spark, str(path)).repartition(3).orderBy("source_row_index").collect()
    assert first == second
    assert [r.transaction_id for r in first] == ["HI-Small:0", "HI-Small:1"]
    with pytest.raises(ValueError, match="one explicit CSV"):
        MODULE.read_transactions(spark, str(tmp_path))


def test_queue_randomized_parity_and_label_independence(spark):
    import numpy as np
    from bda_pipeline.analytics import add_behaviour

    rng = np.random.default_rng(459)
    count = 1800
    local = pd.DataFrame({
        "source_row_index": range(count), "transaction_id": [f"HI-Small:{i}" for i in range(count)],
        "timestamp": pd.Timestamp("2022-09-01") + pd.to_timedelta(rng.integers(0, 18000, count), unit="m"),
        "from_bank": rng.choice(["20", "020", "21"], count),
        "from_account": rng.choice(["0001", "0002", "0003"], count),
        "to_bank": rng.choice(["22", "022", "23"], count),
        "to_account": rng.choice(["0001", "0002", "0003"], count),
        "is_laundering": rng.integers(0, 2, count),
    })
    expected = add_behaviour(local)
    from pyspark.sql import functions as F
    source = spark.createDataFrame(local).repartition(5)
    actual = MODULE.add_behaviour(source).orderBy("source_row_index").toPandas()
    pd.testing.assert_frame_equal(actual, expected, check_dtype=False)
    changed = MODULE.add_behaviour(source.withColumn("is_laundering", 1 - F.col("is_laundering")))
    features = [c for c in actual if c.startswith(("txn_count_", "distinct_counterparties_",
                                                    "in_txn_count_", "distinct_senders_"))]
    pd.testing.assert_frame_equal(changed.orderBy("source_row_index").toPandas()[features], actual[features])


@pytest.mark.parametrize("field,value", [("timestamp", "invalid"), ("amount_paid", "-1"),
                                         ("amount_received", "NaN"), ("is_laundering", "2"),
                                         ("from_account", "missing")])
def test_batched_validation_still_rejects_bad_rows(spark, field, value):
    values = ["2022/09/01 00:00", "020", "0007", "20", "0007", "10", "US Dollar", "10", "US Dollar", "ACH", "0"]
    values[MODULE.TRANSACTION_COLUMNS.index(field)] = value
    raw = spark.createDataFrame([values], MODULE.TRANSACTION_COLUMNS)
    accounts = spark.createDataFrame([("A", "20", "0007", "E1", "Alice")], list(MODULE.ACCOUNT_COLUMNS))
    fx = spark.createDataFrame([("2022-09-01", "US Dollar", "1")], ["txn_date", "currency", "usd_rate"])
    with pytest.raises(ValueError):
        MODULE.curate(raw, accounts, fx)
