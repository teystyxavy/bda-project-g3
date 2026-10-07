"""Local Spark history benchmark. No AWS calls; force evaluation of all features.

Purpose
-------
The Glue curation job (``jobs/curate_transactions.py``) computes five
trailing "behaviour" features for every transaction, per sender account
(normalised bank id + account number):

* ``txn_count_1h`` / ``txn_count_24h`` / ``txn_count_7d`` - how many earlier
  transactions the sender made in the preceding 1 hour / 24 hours / 7 days;
* ``distinct_counterparties_24h`` / ``distinct_counterparties_7d`` - how many
  different receiving (bank, account) pairs the sender paid in that window.

These are AML signals for BQ1 (bursts of activity, fan-out to many
counterparties). The first implementation used Spark window functions with
``rangeBetween`` frames, which rebuild a ``collect_set`` for every row and
were slow on busy senders. The job now uses ``add_behaviour`` (a sorted,
queue-based partition scan). This script measures both implementations and checks parity for the tested
inputs. A speedup on one benchmark does not guarantee a speedup on every run.

What it does
------------
1. Builds a synthetic "dense" worst case (one sender, timestamps repeated
   three times per minute, 1,000 cycling counterparties) and times the old
   window version vs. the new queue version, requiring identical checksums.
2. Optionally (``--transactions``) runs the new version on the full raw
   HI-Small CSV, profiles sender sizes and times it.
3. Optionally (``--reference``) compares every feature value row-by-row
   against a saved Parquet produced by the earlier local pandas pipeline.

Inputs/outputs: local files only. Writes a JSON list of timing/parity
results to ``--report`` (must not already exist). Runs on a local PySpark
installation; nothing is sent to S3 or Glue.

Usage::

    python scripts/benchmarks/benchmark-glue-history.py --report out.json \\
        [--transactions dataset/HI-Small_Trans.csv] [--reference ref.parquet] \\
        [--dense-rows 24000]
"""
import argparse
import importlib.util
import json
from pathlib import Path
from time import perf_counter

from pyspark.sql import SparkSession, Window, functions as F

# Repository root (this file is in <root>/scripts/benchmarks/).
ROOT = Path(__file__).resolve().parents[2]
# Load the real Glue job file as a module so the benchmark exercises exactly
# the production code (normalized_bank, read_transactions, add_behaviour)
# rather than a copy. Importing it does not start the Glue job itself.
SPEC = importlib.util.spec_from_file_location("curation", ROOT / "jobs/curate_transactions.py")
JOB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JOB)
# The five behaviour columns compared between implementations.
FEATURES = ["txn_count_1h", "txn_count_24h", "txn_count_7d",
            "distinct_counterparties_24h", "distinct_counterparties_7d"]


def original_windows(frame):
    """Reference implementation of the behaviour features using Spark windows.

    This is the *original* (slower) approach, kept here only as a baseline
    for timing and correctness comparison.

    For each sender, rows are ordered by epoch seconds and, for each window
    length, a range frame ``rangeBetween(-seconds, -1)`` selects earlier rows
    whose timestamp lies in ``[t - seconds, t - 1]``. The upper bound of -1
    means transactions at the *same second* as the current row are excluded,
    so the feature only uses strictly earlier history (no look-ahead and no
    leakage from simultaneous rows).

    Args:
        frame: Spark DataFrame with transaction_id, timestamp, from_bank,
            from_account, to_bank and to_account columns.

    Returns:
        The input columns plus the five FEATURES columns.
    """
    # Temporary helper columns: bank ids without leading zeros (so '020' and
    # '20' are the same bank) and timestamp as integer seconds for range frames.
    result = frame.withColumn("_bank", JOB.normalized_bank(F.col("from_bank"))).withColumn(
        "_party", JOB.normalized_bank(F.col("to_bank"))).withColumn("_epoch", F.col("timestamp").cast("long"))
    # One window per sender account, ordered by time.
    window = Window.partitionBy("_bank", "from_account").orderBy("_epoch")
    # 1h = 3,600 s, 24h = 86,400 s, 7d = 604,800 s.
    for suffix, seconds in [("1h", 3600), ("24h", 86400), ("7d", 604800)]:
        history = window.rangeBetween(-seconds, -1)
        result = result.withColumn(f"txn_count_{suffix}", F.count("transaction_id").over(history))
            # Distinct counterparties are only defined for 24h and 7d. The set of
            # (receiving bank, receiving account) structs is collected and its
            # size taken - this per-row set rebuild is what made it slow.
        if suffix != "1h":
            result = result.withColumn(f"distinct_counterparties_{suffix}", F.size(
                F.collect_set(F.struct("_party", "to_account")).over(history)).cast("long"))
    # Remove helper columns so the output schema matches the production job.
    return result.drop("_bank", "_party", "_epoch")


def measure(name, result):
    """Force full evaluation of ``result`` and time it.

    Spark is lazy, so nothing is computed until an action runs. Summing every
    feature column (plus a row count) forces every feature of every row to be
    calculated, and the sums double as a cheap checksum for comparing two
    implementations.

    Args:
        name: label stored in the report (e.g. ``'dense_queue'``).
        result: Spark DataFrame containing the FEATURES columns.

    Returns:
        dict with ``name``, wall-clock ``seconds`` and ``totals`` (row count
        and per-feature sums). Also printed as one JSON line.
    """
    start = perf_counter()
    totals = result.agg(F.count("*").alias("rows"), *[F.sum(c).alias(c) for c in FEATURES]).first().asDict()
    report = dict(name=name, seconds=round(perf_counter() - start, 3), totals=totals)
    print(json.dumps(report), flush=True)
    return report


def main():
    """Parse arguments, run the benchmarks and write the JSON report.

    Side effects: starts a local SparkSession, prints progress JSON lines,
    and creates ``--report`` exclusively (``open('x')``) so an earlier result
    is never overwritten. Raises ValueError if any checksum/parity differs.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transactions", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reference", type=Path, help="Saved local cohort Parquet for exact feature comparison")
    parser.add_argument("--dense-rows", type=int, default=24000)
    args = parser.parse_args()
    # Refuse to overwrite evidence from a previous benchmark run.
    if args.report.exists():
        parser.error("Use a fresh report file")
    # Match the Glue job's settings: UTC session time zone (so epoch seconds
    # and dates do not shift with the laptop's local time zone) and a small
    # shuffle partition count suited to a single machine.
    spark = SparkSession.builder.appName("history-benchmark").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.shuffle.partitions", "8")
    # Repeated timestamps, repeated counterparties, and all history within 7d.
    # Synthetic dense case: id/3 * 60 s gives three transactions per minute
    # (repeated timestamps), one sender ('020', '0001'), and 1,000 cycling
    # counterparties. 24,000 rows span ~5.5 days, so all history stays inside
    # the 7-day window - the most expensive scenario for window frames.
    # 1661990400 is 2022-09-01 00:00:00 UTC, the dataset's first day.
    dense = spark.range(args.dense_rows).selectExpr(
        "id as source_row_index", "concat('HI-Small:', id) as transaction_id",
        "timestamp_seconds(1661990400 + cast(id / 3 as bigint) * 60) as timestamp",
        "'020' as from_bank", "'0001' as from_account", "'021' as to_bank",
        "cast(id % 1000 as string) as to_account").cache()
    # Materialise the cache so data generation is not counted in the timings.
    dense.count()
    results = [measure("dense_original", original_windows(dense)),
               measure("dense_queue", JOB.add_behaviour(dense))]
    # Both implementations must agree exactly before speed is reported.
    if results[0]["totals"] != results[1]["totals"]:
        raise ValueError("Dense benchmark feature checksums differ")
    # Ratio > 1 means the queue version is faster.
    results.append({"dense_speedup": results[0]["seconds"] / results[1]["seconds"]})
    dense.unpersist()
    # Optional: full real dataset (about 5.08 million rows for HI-Small).
    if args.transactions:
        start = perf_counter()
        # read_transactions enforces the exact CSV header and assigns a stable
        # source_row_index; the raw timestamp text is 'yyyy/MM/dd HH:mm'.
        frame = JOB.read_transactions(spark, str(args.transactions)).select(
            "source_row_index", "transaction_id", "from_bank", "from_account", "to_bank", "to_account",
            F.to_timestamp("timestamp", "yyyy/MM/dd HH:mm").alias("timestamp")).cache()
        rows = frame.count()
        # Profile sender sizes: the largest sender history drives the memory
        # needed by the queue scan, so record it alongside the timing.
        sizes = frame.groupBy(JOB.normalized_bank(F.col("from_bank")).alias("bank"), "from_account").count()
        profile = sizes.agg(F.count("*").alias("senders"), F.max("count").alias("max_sender_rows")).first().asDict()
        results.append(dict(name="full_input_profile", rows=rows, seconds=round(perf_counter()-start, 3), **profile))
        print(json.dumps(results[-1]), flush=True)
        full = JOB.add_behaviour(frame).cache()
        results.append(measure("full_queue", full))
        if args.reference:
            # Exact parity check against a saved cohort: join on source_row_index
            # and count rows where any feature differs (eqNullSafe treats
            # null == null as equal). Both counts must match for success.
            # Read only the integer fields; older pandas references contain a
            # nanosecond timestamp that Spark 3.5 cannot infer from full schema.
            schema = ", ".join(f"{name} long" for name in ["source_row_index", *FEATURES])
            reference = spark.read.schema(schema).parquet(str(args.reference))
            expected_count = reference.count()
            compared = full.alias("actual").join(reference.alias("expected"), "source_row_index")
            mismatch = F.lit(False)
            # Build a boolean "any feature differs" expression across all five features.
            for name in FEATURES:
                mismatch = mismatch | ~F.col(f"actual.{name}").eqNullSafe(F.col(f"expected.{name}"))
            checks = compared.agg(F.count("*").alias("rows"), F.sum(mismatch.cast("long")).alias("mismatches")).first().asDict()
            if checks["rows"] != expected_count or checks["mismatches"] != 0:
                raise ValueError(f"Cohort parity failed: {checks}")
            results.append(dict(name="full_cohort_parity", **checks))
            print(json.dumps(results[-1]), flush=True)
        # Release cached data before writing the report.
        full.unpersist()
        frame.unpersist()
    # 'x' mode = create only; fails if the file appeared in the meantime.
    with args.report.open("x", encoding="utf-8") as target:
        json.dump(results, target, indent=2)
    spark.stop()


if __name__ == "__main__":
    main()
