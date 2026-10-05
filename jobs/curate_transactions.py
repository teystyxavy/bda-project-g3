"""Glue 5.0 curation foundation; runnable with spark-submit for local parity tests."""

import argparse
import csv
import json
from functools import reduce
from time import perf_counter

from pyspark.sql import SparkSession, functions as functions
from pyspark.sql.types import LongType, StringType, StructField, StructType


TRANSACTION_COLUMNS = [
    "timestamp", "from_bank", "from_account", "to_bank", "to_account",
    "amount_received", "receiving_currency", "amount_paid", "payment_currency",
    "payment_format", "is_laundering",
]
RAW_HEADER = [
    "Timestamp", "From Bank", "Account", "To Bank", "Account", "Amount Received",
    "Receiving Currency", "Amount Paid", "Payment Currency", "Payment Format", "Is Laundering",
]
ACCOUNT_COLUMNS = {
    "Bank Name": "bank_name", "Bank ID": "bank_id", "Account Number": "account_number",
    "Entity ID": "entity_id", "Entity Name": "entity_name",
}


def reject(frame, condition, message):
    if frame.filter(condition).limit(1).count():
        raise ValueError(message)


def reject_many(frame, checks):
    """Evaluate related quality gates in one scan, keeping fail-closed nulls."""
    failures = frame.agg(*[
        functions.max(functions.coalesce(condition, functions.lit(True)).cast("int")).alias(f"check_{i}")
        for i, (condition, _) in enumerate(checks)
    ]).first()
    for i, (_, message) in enumerate(checks):
        if failures[i]:
            raise ValueError(message)


def normalized_bank(column):
    stripped = functions.regexp_replace(column, "^0+", "")
    return functions.when(stripped == "", "0").otherwise(stripped)


def read_transactions(spark, path):
    # A single immutable, one-record-per-line file is the source identity contract.
    # zipWithIndex preserves physical row order, unlike monotonically_increasing_id.
    jpath = spark._jvm.org.apache.hadoop.fs.Path(path)
    if not jpath.getFileSystem(spark._jsc.hadoopConfiguration()).isFile(jpath):
        raise ValueError("Transactions must be one explicit CSV file")
    lines = spark.sparkContext.textFile(path)
    header = next(csv.reader([lines.first()], strict=True))
    if header != RAW_HEADER:
        raise ValueError("Unexpected transaction CSV header or column order")
    def parse(record):
        line, index = record
        fields = next(csv.reader([line], strict=True))
        if len(fields) != len(TRANSACTION_COLUMNS):
            raise ValueError(f"Invalid CSV record at source row {index - 1}")
        return (*fields, index - 1, f"HI-Small:{index - 1}")
    schema = StructType([StructField(name, StringType()) for name in TRANSACTION_COLUMNS] + [
        StructField("source_row_index", LongType(), False),
        StructField("transaction_id", StringType(), False),
    ])
    return spark.createDataFrame(lines.zipWithIndex().filter(lambda row: row[1] > 0).map(parse), schema)


def add_behaviour(frame):
    """Sort once, then maintain exact queues in a streaming partition scan.

    Each row enters/leaves each queue once instead of rebuilding distinct sets
    for overlapping range frames. State resets per normalized sender; a cached
    timestamp snapshot excludes all simultaneous peers without buffering them.
    Input must contain complete sender histories. Memory follows active history,
    not the entire partition. Labels and USD values do not enter the calculation.
    """
    result = frame.withColumn("_sender_bank", normalized_bank(functions.col("from_bank")))
    result = result.withColumn("_party_bank", normalized_bank(functions.col("to_bank")))
    result = result.withColumn("_epoch", functions.col("timestamp").cast("long"))
    ordered = result.repartition("_sender_bank", "from_account").sortWithinPartitions(
        "_sender_bank", "from_account", "_epoch", "source_row_index")
    columns = ordered.columns
    sender_bank, sender_account, party_bank, party_account, epoch = [
        columns.index(name) for name in ["_sender_bank", "from_account", "_party_bank", "to_account", "_epoch"]]
    width = len(frame.columns)

    def scan(records):
        from collections import Counter, deque

        sender = None
        now = None
        for row in records:
            key = (row[sender_bank], row[sender_account])
            if key != sender:
                sender, now = key, None
                queues = [deque(), deque(), deque()]
                counts = [Counter(), Counter(), Counter()]
            timestamp = row[epoch]
            if timestamp != now:
                now = timestamp
                for queue, counter, seconds in zip(queues, counts, (3600, 86400, 604800)):
                    while queue and queue[0][0] < now - seconds:
                        _, party = queue.popleft()
                        counter[party] -= 1
                        if not counter[party]:
                            del counter[party]
                snapshot = tuple(len(queue) for queue in queues) + (len(counts[1]), len(counts[2]))
            yield tuple(row[:width]) + snapshot
            party = (row[party_bank], row[party_account])
            for queue, counter in zip(queues, counts):
                queue.append((timestamp, party))
                counter[party] += 1

    names = ["txn_count_1h", "txn_count_24h", "txn_count_7d",
             "distinct_counterparties_24h", "distinct_counterparties_7d"]
    schema = StructType(list(frame.schema.fields) + [StructField(name, LongType(), False) for name in names])
    return frame.sparkSession.createDataFrame(ordered.rdd.mapPartitions(scan), schema)


def curate(transactions, accounts, rates):
    missing = set(ACCOUNT_COLUMNS) - set(accounts.columns)
    if missing:
        raise ValueError(f"Missing account columns: {sorted(missing)}")
    if not {"txn_date", "currency", "usd_rate"}.issubset(rates.columns):
        raise ValueError("Missing FX columns")
    required = reduce(lambda left, right: left | right, [
        functions.col(name).isNull() | (functions.trim(functions.col(name)) == "")
        for name in TRANSACTION_COLUMNS
    ])
    result = transactions.withColumn("timestamp", functions.to_timestamp("timestamp", "yyyy/MM/dd HH:mm"))
    result = result.withColumn("txn_date", functions.to_date("timestamp"))
    checks = [(required, "Required transaction fields are empty"),
              (functions.col("timestamp").isNull(), "Invalid timestamp"),
              (~functions.col("txn_date").between("2022-09-01", "2022-09-18"), "Date outside HI-Small range"),
              (~functions.col("is_laundering").isin("0", "1"), "Invalid label")]
    for name in ["amount_paid", "amount_received"]:
        result = result.withColumn(name, functions.col(name).cast("double"))
        checks.append((functions.col(name).isNull() | functions.isnan(name) |
                       (functions.abs(functions.col(name)) == float("inf")) | (functions.col(name) < 0), "Invalid amount"))
    reject_many(result, checks)
    result = result.withColumn("is_laundering", functions.col("is_laundering").cast("int"))
    metadata = accounts.select(*[functions.col(source).alias(target) for source, target in ACCOUNT_COLUMNS.items()])
    metadata = metadata.withColumn("bank_key", normalized_bank(functions.col("bank_id")))
    reject(metadata, functions.col("bank_key").isNull() | functions.col("account_number").isNull() |
           (functions.col("bank_id") == "") | (functions.col("account_number") == ""), "Invalid account key")
    reject(metadata.groupBy("bank_key", "account_number").count(), functions.col("count") > 1,
           "Duplicate normalized composite account key")
    for side in ["from", "to"]:
        bank_key = f"_{side}_bank_key"
        result = result.withColumn(bank_key, normalized_bank(functions.col(f"{side}_bank")))
        side_metadata = metadata.select(
            functions.col("bank_key").alias(bank_key),
            functions.col("account_number").alias(f"{side}_account"),
            *[functions.col(name).alias(f"{side}_{name}") for name in ["bank_name", "entity_id", "entity_name"]],
            functions.lit(True).alias(f"_{side}_matched"),
        )
        result = result.join(side_metadata, [bank_key, f"{side}_account"], "left")
        result = result.drop(bank_key)
    rates = rates.select(
        functions.to_date("txn_date").alias("rate_day"), "currency",
        functions.col("usd_rate").cast("double").alias("usd_rate"),
    )
    reject(rates, functions.col("rate_day").isNull() | functions.col("currency").isNull() |
           (functions.col("currency") == "") | functions.col("usd_rate").isNull() |
           functions.isnan("usd_rate") | (functions.col("usd_rate") <= 0) |
           (functions.col("usd_rate") == float("inf")), "Invalid FX rate")
    reject(rates.groupBy("rate_day", "currency").count(), functions.col("count") > 1, "Duplicate FX key")
    for output_name, days_back in [("amount_usd", 0), ("amount_usd_asof", 1)]:
        result = result.join(functions.broadcast(rates),
                             (functions.date_sub(result.txn_date, days_back) == rates.rate_day) &
                             (result.payment_currency == rates.currency), "left")
        effective_rate = functions.when(functions.col("payment_currency") == "US Dollar", 1.0).otherwise(functions.col("usd_rate"))
        result = result.withColumn(output_name, functions.col("amount_paid") * effective_rate)
        result = result.drop("rate_day", "currency", "usd_rate")
    checks = [(functions.col(f"_{side}_matched").isNull(), f"Unmatched {side} account") for side in ["from", "to"]]
    for name in ["amount_usd", "amount_usd_asof"]:
        checks.append((functions.col(name).isNull() | functions.isnan(name) |
                       (functions.abs(functions.col(name)) == float("inf")), f"Missing FX or nonfinite {name}"))
    reject_many(result, checks)
    result = result.drop("_from_matched", "_to_matched")
    return result.withColumn("is_self_txn", (functions.col("from_bank") == functions.col("to_bank")) &
                             (functions.col("from_account") == functions.col("to_account"))).withColumn(
        "currency_mismatch", functions.col("payment_currency") != functions.col("receiving_currency"))


def main():
    parser = argparse.ArgumentParser()
    for name in ["transactions", "accounts", "fx", "output"]:
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--expected_rows", type=int, required=True)
    parser.add_argument("--behaviour", choices=["true", "false"], default="false")
    args, _ = parser.parse_known_args()
    if args.expected_rows <= 0:
        raise ValueError("expected_rows must be positive")
    spark = SparkSession.builder.appName("is459-curation").getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "false")
    spark.conf.set("spark.sql.shuffle.partitions", "8")
    started = perf_counter()
    transactions = read_transactions(spark, args.transactions).cache()
    raw_rows = transactions.count()
    if raw_rows != args.expected_rows:
        raise ValueError(f"Expected {args.expected_rows} raw rows, received {raw_rows}")
    loaded = perf_counter()
    print(json.dumps({"phase": "input", "rows": raw_rows, "seconds": round(loaded-started, 3)}), flush=True)
    accounts = spark.read.options(header=True, mode="FAILFAST").csv(args.accounts)
    rates = spark.read.options(header=True, mode="FAILFAST").csv(args.fx)
    result = curate(transactions, accounts, rates).cache()
    if result.count() != raw_rows:
        raise ValueError("Account/FX joins changed the row count")
    curated = perf_counter()
    transactions.unpersist()
    print(json.dumps({"phase": "curation", "seconds": round(curated-loaded, 3)}), flush=True)
    if args.behaviour == "true":
        result = add_behaviour(result)
    result.write.mode("errorifexists").partitionBy("txn_date").parquet(args.output)
    written = spark.read.parquet(args.output)
    if written.count() != raw_rows:
        raise ValueError("Written row count differs from input")
    print(json.dumps({"phase": "features_write_verify", "seconds": round(perf_counter()-curated, 3),
                      "total_seconds": round(perf_counter()-started, 3)}), flush=True)
    print(f"CURATION_VERIFIED rows={raw_rows} output={args.output}")
    spark.stop()


if __name__ == "__main__":
    main()
