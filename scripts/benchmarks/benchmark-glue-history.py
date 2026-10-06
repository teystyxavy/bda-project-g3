"""Local Spark history benchmark. No AWS calls; force evaluation of all features."""
import argparse
import importlib.util
import json
from pathlib import Path
from time import perf_counter

from pyspark.sql import SparkSession, Window, functions as F

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("curation", ROOT / "jobs/curate_transactions.py")
JOB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(JOB)
FEATURES = ["txn_count_1h", "txn_count_24h", "txn_count_7d",
            "distinct_counterparties_24h", "distinct_counterparties_7d"]


def original_windows(frame):
    result = frame.withColumn("_bank", JOB.normalized_bank(F.col("from_bank"))).withColumn(
        "_party", JOB.normalized_bank(F.col("to_bank"))).withColumn("_epoch", F.col("timestamp").cast("long"))
    window = Window.partitionBy("_bank", "from_account").orderBy("_epoch")
    for suffix, seconds in [("1h", 3600), ("24h", 86400), ("7d", 604800)]:
        history = window.rangeBetween(-seconds, -1)
        result = result.withColumn(f"txn_count_{suffix}", F.count("transaction_id").over(history))
        if suffix != "1h":
            result = result.withColumn(f"distinct_counterparties_{suffix}", F.size(
                F.collect_set(F.struct("_party", "to_account")).over(history)).cast("long"))
    return result.drop("_bank", "_party", "_epoch")


def measure(name, result):
    start = perf_counter()
    totals = result.agg(F.count("*").alias("rows"), *[F.sum(c).alias(c) for c in FEATURES]).first().asDict()
    report = dict(name=name, seconds=round(perf_counter() - start, 3), totals=totals)
    print(json.dumps(report), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transactions", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--reference", type=Path, help="Saved local cohort Parquet for exact feature comparison")
    parser.add_argument("--dense-rows", type=int, default=24000)
    args = parser.parse_args()
    if args.report.exists():
        parser.error("Use a fresh report file")
    spark = SparkSession.builder.appName("history-benchmark").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.shuffle.partitions", "8")
    # Repeated timestamps, repeated counterparties, and all history within 7d.
    dense = spark.range(args.dense_rows).selectExpr(
        "id as source_row_index", "concat('HI-Small:', id) as transaction_id",
        "timestamp_seconds(1661990400 + cast(id / 3 as bigint) * 60) as timestamp",
        "'020' as from_bank", "'0001' as from_account", "'021' as to_bank",
        "cast(id % 1000 as string) as to_account").cache()
    dense.count()
    results = [measure("dense_original", original_windows(dense)),
               measure("dense_queue", JOB.add_behaviour(dense))]
    if results[0]["totals"] != results[1]["totals"]:
        raise ValueError("Dense benchmark feature checksums differ")
    results.append({"dense_speedup": results[0]["seconds"] / results[1]["seconds"]})
    dense.unpersist()
    if args.transactions:
        start = perf_counter()
        frame = JOB.read_transactions(spark, str(args.transactions)).select(
            "source_row_index", "transaction_id", "from_bank", "from_account", "to_bank", "to_account",
            F.to_timestamp("timestamp", "yyyy/MM/dd HH:mm").alias("timestamp")).cache()
        rows = frame.count()
        sizes = frame.groupBy(JOB.normalized_bank(F.col("from_bank")).alias("bank"), "from_account").count()
        profile = sizes.agg(F.count("*").alias("senders"), F.max("count").alias("max_sender_rows")).first().asDict()
        results.append(dict(name="full_input_profile", rows=rows, seconds=round(perf_counter()-start, 3), **profile))
        print(json.dumps(results[-1]), flush=True)
        full = JOB.add_behaviour(frame).cache()
        results.append(measure("full_queue", full))
        if args.reference:
            # Read only the integer fields; older pandas references contain a
            # nanosecond timestamp that Spark 3.5 cannot infer from full schema.
            schema = ", ".join(f"{name} long" for name in ["source_row_index", *FEATURES])
            reference = spark.read.schema(schema).parquet(str(args.reference))
            expected_count = reference.count()
            compared = full.alias("actual").join(reference.alias("expected"), "source_row_index")
            mismatch = F.lit(False)
            for name in FEATURES:
                mismatch = mismatch | ~F.col(f"actual.{name}").eqNullSafe(F.col(f"expected.{name}"))
            checks = compared.agg(F.count("*").alias("rows"), F.sum(mismatch.cast("long")).alias("mismatches")).first().asDict()
            if checks["rows"] != expected_count or checks["mismatches"] != 0:
                raise ValueError(f"Cohort parity failed: {checks}")
            results.append(dict(name="full_cohort_parity", **checks))
            print(json.dumps(results[-1]), flush=True)
        full.unpersist()
        frame.unpersist()
    with args.report.open("x", encoding="utf-8") as target:
        json.dump(results, target, indent=2)
    spark.stop()


if __name__ == "__main__":
    main()
