import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("pyspark")
from pyspark.sql import SparkSession

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from network_fixture import factory, network_frame  # noqa: E402

from bda_pipeline.network import TABLES, analyse_network, compare_network, read_tables  # noqa: E402

SPEC = importlib.util.spec_from_file_location("glue_network", ROOT / "jobs" / "network_mules.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture(scope="module")
def spark():
    session = SparkSession.builder.master("local[2]").appName("network-parity").getOrCreate()
    session.conf.set("spark.sql.session.timeZone", "UTC")
    session.conf.set("spark.sql.ansi.enabled", "false")
    session.conf.set("spark.sql.shuffle.partitions", "2")
    yield session
    session.stop()


def test_glue_network_job_matches_pandas_reference(spark, tmp_path):
    frame = network_frame()
    curated = tmp_path / "curated"
    # Microsecond UTC timestamps, as the curate job writes them (Spark cannot read nanoseconds).
    utc = frame.assign(timestamp=frame.timestamp.dt.tz_localize("UTC"), txn_date=frame.timestamp.dt.strftime("%Y-%m-%d"))
    utc.to_parquet(curated, partition_cols=["txn_date"], coerce_timestamps="us")
    tables = MODULE.analyse(spark.read.parquet(str(curated)), spark, len(frame))
    output = tmp_path / "network"
    for name in MODULE.TABLE_NAMES:
        tables[name].coalesce(1).write.parquet(str(output / name))
    MODULE.write_success(spark, str(output))
    assert (output / "_SUCCESS").exists()
    actual = read_tables(output)
    assert set(actual) == set(TABLES)
    proof = compare_network(analyse_network(factory(frame)), actual, len(frame))
    assert proof["status"] == "passed" and proof["table_rows"]["loops"] == 2


def test_glue_network_job_rejects_row_count_mismatch(spark, tmp_path):
    frame = network_frame()
    frame.assign(timestamp=frame.timestamp.dt.tz_localize("UTC")).to_parquet(tmp_path / "curated.parquet", coerce_timestamps="us")
    with pytest.raises(ValueError, match="curated rows"):
        MODULE.analyse(spark.read.parquet(str(tmp_path / "curated.parquet")), spark, len(frame) + 1)
