"""Glue 5.0 PySpark job turning the raw Kaggle files (transactions, accounts, FX CSVs) into curated Parquet partitioned by txn_date; also runnable with spark-submit for local parity tests.
Arguments: --transactions --accounts --fx --output --expected_rows [--behaviour true]; every check raises ValueError so Glue fails the run before downstream steps see partial data."""

import argparse
import csv
import json
from functools import reduce
from time import perf_counter

from pyspark.sql import SparkSession, functions as functions
from pyspark.sql.types import LongType, StringType, StructField, StructType


# Snake-case names given to the 11 CSV columns, in file order.
TRANSACTION_COLUMNS = [
    "timestamp", "from_bank", "from_account", "to_bank", "to_account",
    "amount_received", "receiving_currency", "amount_paid", "payment_currency",
    "payment_format", "is_laundering",
]
# Exact Kaggle header; two columns are both named "Account", so the file is parsed by position.
RAW_HEADER = [
    "Timestamp", "From Bank", "Account", "To Bank", "Account", "Amount Received",
    "Receiving Currency", "Amount Paid", "Payment Currency", "Payment Format", "Is Laundering",
]
# Accounts file column -> curated column name.
ACCOUNT_COLUMNS = {
    "Bank Name": "bank_name", "Bank ID": "bank_id", "Account Number": "account_number",
    "Entity ID": "entity_id", "Entity Name": "entity_name",
}


def reject(frame, condition, message):
    """Raises ``ValueError(message)`` if any row of ``frame`` matches ``condition`` (stops at the first offending row)."""
    if frame.filter(condition).limit(1).count():
        raise ValueError(message)


def reject_many(frame, checks):
    """Evaluates related quality gates in one scan; a NULL condition counts as a failure so unknown data never passes silently.
    ``checks`` is a list of (condition, message) pairs, and the first failing message is raised."""
    failures = frame.agg(*[
        functions.max(functions.coalesce(condition, functions.lit(True)).cast("int")).alias(f"check_{i}")
        for i, (condition, _) in enumerate(checks)
    ]).first()
    for i, (_, message) in enumerate(checks):
        if failures[i]:
            raise ValueError(message)


def normalized_bank(column):
    """Spark expression: bank code with leading zeros removed ('' -> '0'), the same rule as ``bank_key`` in ``analytics.py``.
    The transactions and accounts files pad bank IDs differently, so both sides are normalised before joining."""
    stripped = functions.regexp_replace(column, "^0+", "")
    return functions.when(stripped == "", "0").otherwise(stripped)


def read_transactions(spark, path):
    """Read the transactions CSV with stable row numbers.

    Args:
        spark: active SparkSession.
        path: path/URI of ONE CSV file (not a folder).

    Returns:
        DataFrame with the 11 TRANSACTION_COLUMNS as strings, plus
        ``source_row_index`` (long; 0 = first data row after the header) and
        ``transaction_id`` (``'HI-Small:<source_row_index>'``).

    Raises:
        ValueError: if the path is not a single file, the header differs from
        RAW_HEADER, or a line does not have exactly 11 CSV fields.
    """
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
        """Split one (line, line_number) pair into fields + identity columns."""
        line, index = record
        fields = next(csv.reader([line], strict=True))
        if len(fields) != len(TRANSACTION_COLUMNS):
            raise ValueError(f"Invalid CSV record at source row {index - 1}")
        # Line 0 is the header, so data row number = line number - 1.
        return (*fields, index - 1, f"{subset}:{index - 1}")
    schema = StructType([StructField(name, StringType()) for name in TRANSACTION_COLUMNS] + [
        StructField("source_row_index", LongType(), False),
        StructField("transaction_id", StringType(), False),
    ])
    # filter(row[1] > 0) drops the header line before parsing.
    return spark.createDataFrame(lines.zipWithIndex().filter(lambda row: row[1] > 0).map(parse), schema)


def _windowed_counts(frame, self_bank_col, self_account_col, party_bank_col, party_account_col, names):
    """Sorts once, then scans each entity's history with exact window queues, excluding same-timestamp peers.
    Returns ``source_row_index`` plus the five ``names`` columns; needs complete history per entity."""
    result = frame.withColumn("_epoch", functions.col("timestamp").cast("long"))
    # Co-locate each entity's rows, sorted by time (source_row_index breaks ties), for one sequential scan.
    ordered = result.repartition(self_bank_col, self_account_col).sortWithinPartitions(
        self_bank_col, self_account_col, "_epoch", "source_row_index")
    columns = ordered.columns
    self_bank, self_account, party_bank, party_account, epoch, row_index = [
        columns.index(name) for name in
        [self_bank_col, self_account_col, party_bank_col, party_account_col, "_epoch", "source_row_index"]]

    def scan(records):
        """Generator run once per partition over the sorted rows."""
        from collections import Counter, deque

        entity = None
        now = None
        for row in records:
            key = (row[self_bank], row[self_account])
            if key != entity:
                # New entity: reset the windows (a deque plus a counterparty Counter per window).
                entity, now = key, None
                queues = [deque(), deque(), deque()]
                counts = [Counter(), Counter(), Counter()]
            timestamp = row[epoch]
            if timestamp != now:
                # Recompute only when time moves forward; same-timestamp rows reuse the snapshot, so peers exclude each other.
                now = timestamp
                # Evict entries older than now - window; entries exactly at the cutoff stay.
                for queue, counter, seconds in zip(queues, counts, (3600, 86400, 604800)):
                    while queue and queue[0][0] < now - seconds:
                        _, party = queue.popleft()
                        counter[party] -= 1
                        if not counter[party]:
                            del counter[party]
                # (count 1h, count 24h, count 7d, distinct 24h, distinct 7d)
                snapshot = tuple(len(queue) for queue in queues) + (len(counts[1]), len(counts[2]))
            yield (row[row_index],) + snapshot
            # Add the current transaction AFTER emitting its features.
            party = (row[party_bank], row[party_account])
            for queue, counter in zip(queues, counts):
                queue.append((timestamp, party))
                counter[party] += 1

    schema = StructType([StructField("source_row_index", LongType(), False)] +
                        [StructField(name, LongType(), False) for name in names])
    return frame.sparkSession.createDataFrame(ordered.rdd.mapPartitions(scan), schema)


def add_behaviour(frame):
    """Adds 5 outgoing (sender) and 5 incoming (receiver) long columns, matching ``analytics.add_behaviour``.
    Runs ``_windowed_counts`` per direction and joins both back on ``source_row_index``."""
    result = frame.withColumn("_sender_bank", normalized_bank(functions.col("from_bank")))
    result = result.withColumn("_party_bank", normalized_bank(functions.col("to_bank")))
    out_names = ["txn_count_1h", "txn_count_24h", "txn_count_7d",
                 "distinct_counterparties_24h", "distinct_counterparties_7d"]
    in_names = ["in_txn_count_1h", "in_txn_count_24h", "in_txn_count_7d",
                "distinct_senders_24h", "distinct_senders_7d"]
    out_features = _windowed_counts(result, "_sender_bank", "from_account", "_party_bank", "to_account", out_names)
    in_features = _windowed_counts(result, "_party_bank", "to_account", "_sender_bank", "from_account", in_names)
    return frame.join(out_features, "source_row_index", "inner").join(in_features, "source_row_index", "inner")


def curate(transactions, accounts, rates):
    """Validate, enrich and USD-convert the transactions.

    Args:
        transactions: output of ``read_transactions`` (string columns).
        accounts: accounts CSV DataFrame (ACCOUNT_COLUMNS headers).
        rates: FX CSV DataFrame with ``txn_date``, ``currency``, ``usd_rate``
            (USD value of one unit of the currency on that day).

    Returns:
        DataFrame with typed timestamp/amounts/label, ``txn_date``,
        ``from_*``/``to_*`` account metadata (bank_name, entity_id,
        entity_name), ``amount_usd``, ``amount_usd_asof``, ``is_self_txn`` and
        ``currency_mismatch``. Row count is unchanged (checked by ``main``).

    Raises:
        ValueError: on missing columns, empty fields, bad timestamps/dates/
        labels/amounts, invalid or duplicate account keys or FX rates, an
        account that cannot be matched, or a missing FX rate.
    """
    missing = set(ACCOUNT_COLUMNS) - set(accounts.columns)
    if missing:
        raise ValueError(f"Missing account columns: {sorted(missing)}")
    if not {"txn_date", "currency", "usd_rate"}.issubset(rates.columns):
        raise ValueError("Missing FX columns")
    # True if ANY transaction field is null or blank.
    required = reduce(lambda left, right: left | right, [
        functions.col(name).isNull() | (functions.trim(functions.col(name)) == "")
        for name in TRANSACTION_COLUMNS
    ])
    # Source format is e.g. "2022/09/01 00:20"; an unparsable value becomes NULL.
    result = transactions.withColumn("timestamp", functions.to_timestamp("timestamp", "yyyy/MM/dd HH:mm"))
    result = result.withColumn("txn_date", functions.to_date("timestamp"))
    checks = [(required, "Required transaction fields are empty"),
              (functions.col("timestamp").isNull(), "Invalid timestamp"),
              (~functions.col("txn_date").between(start_date, end_date), "Date outside selected source range"),
              (~functions.col("is_laundering").isin("0", "1"), "Invalid label")]
    # Amounts must parse as finite, non-negative numbers.
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
    # (bank, account) must be unique, otherwise the join would duplicate rows.
    reject(metadata.groupBy("bank_key", "account_number").count(), functions.col("count") > 1,
           "Duplicate normalized composite account key")
    # Join twice (sender, receiver); a left join keeps every transaction and a NULL _matched flag marks a missing account.
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
    # Rates must be present, positive and finite, with one rate per day+currency.
    reject(rates, functions.col("rate_day").isNull() | functions.col("currency").isNull() |
           (functions.col("currency") == "") | functions.col("usd_rate").isNull() |
           functions.isnan("usd_rate") | (functions.col("usd_rate") <= 0) |
           (functions.col("usd_rate") == float("inf")), "Invalid FX rate")
    reject(rates.groupBy("rate_day", "currency").count(), functions.col("count") > 1, "Duplicate FX key")
    # days_back 0 = same-day rate (amount_usd, treasury); 1 = previous day's rate (amount_usd_asof, scoring, so no unknown rate is used).
    # The small rate table is broadcast to every executor.
    for output_name, days_back in [("amount_usd", 0), ("amount_usd_asof", 1)]:
        result = result.join(functions.broadcast(rates),
                             (functions.date_sub(result.txn_date, days_back) == rates.rate_day) &
                             (result.payment_currency == rates.currency), "left")
        # US Dollar always converts at exactly 1.0.
        effective_rate = functions.when(functions.col("payment_currency") == "US Dollar", 1.0).otherwise(functions.col("usd_rate"))
        result = result.withColumn(output_name, functions.col("amount_paid") * effective_rate)
        result = result.drop("rate_day", "currency", "usd_rate")
    # Every account must have matched and every USD value must exist.
    checks = [(functions.col(f"_{side}_matched").isNull(), f"Unmatched {side} account") for side in ["from", "to"]]
    for name in ["amount_usd", "amount_usd_asof"]:
        checks.append((functions.col(name).isNull() | functions.isnan(name) |
                       (functions.abs(functions.col(name)) == float("inf")), f"Missing FX or nonfinite {name}"))
    reject_many(result, checks)
    result = result.drop("_from_matched", "_to_matched")
    # Derived flags used as model features: self-payment and currency mismatch.
    return result.withColumn("is_self_txn", (functions.col("from_bank") == functions.col("to_bank")) &
                             (functions.col("from_account") == functions.col("to_account"))).withColumn(
        "currency_mismatch", functions.col("payment_currency") != functions.col("receiving_currency"))


def main():
    """Parses job arguments, runs curation, writes the Parquet output and re-reads it to confirm the row count.
    Prints JSON timing lines per phase and a final ``CURATION_VERIFIED`` line."""
    parser = argparse.ArgumentParser()
    for name in ["transactions", "accounts", "fx", "output"]:
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--expected_rows", type=int, required=True)
    parser.add_argument("--subset", choices=["HI-Small", "HI-Medium"], default="HI-Small")
    parser.add_argument("--start_date", default="2022-09-01")
    parser.add_argument("--end_date", default="2022-09-18")
    parser.add_argument("--shuffle_partitions", type=int, default=8)
    parser.add_argument("--behaviour", choices=["true", "false"], default="false")
    # parse_known_args ignores Glue's own system arguments (e.g. --JOB_NAME).
    args, _ = parser.parse_known_args()
    if args.expected_rows <= 0:
        raise ValueError("expected_rows must be positive")
    spark = SparkSession.builder.appName("is459-curation").getOrCreate()
    # UTC session time zone so timestamps do not shift with the cluster's zone.
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    # Non-ANSI: a bad cast yields NULL, which the explicit checks then report clearly.
    spark.conf.set("spark.sql.ansi.enabled", "false")
    # Small job on 2 workers: 8 shuffle partitions instead of Spark's default 200.
    if not 1 <= args.shuffle_partitions <= 128:
        raise ValueError("Invalid shuffle partition bound")
    spark.conf.set("spark.sql.shuffle.partitions", str(args.shuffle_partitions))
    started = perf_counter()
    # cache(): the transactions are used for the count and for curation.
    transactions = read_transactions(spark, args.transactions, args.subset).cache()
    raw_rows = transactions.count()
    if raw_rows != args.expected_rows:
        raise ValueError(f"Expected {args.expected_rows} raw rows, received {raw_rows}")
    loaded = perf_counter()
    print(json.dumps({"phase": "input", "rows": raw_rows, "seconds": round(loaded-started, 3)}), flush=True)
    # FAILFAST: a malformed CSV line raises immediately instead of being dropped.
    accounts = spark.read.options(header=True, mode="FAILFAST").csv(args.accounts)
    rates = spark.read.options(header=True, mode="FAILFAST").csv(args.fx)
    result = curate(transactions, accounts, rates, args.start_date, args.end_date).cache()
    # Left joins must not add or lose rows (would mean duplicate keys).
    if result.count() != raw_rows:
        raise ValueError("Account/FX joins changed the row count")
    curated = perf_counter()
    transactions.unpersist()
    print(json.dumps({"phase": "curation", "seconds": round(curated-loaded, 3)}), flush=True)
    if args.behaviour == "true":
        result = add_behaviour(result)
    # errorifexists never overwrites an earlier run; partitionBy writes one txn_date folder per day.
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
