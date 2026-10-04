"""Glue 5.0 curation foundation; runnable with spark-submit for local parity tests."""

import argparse
from functools import reduce

from pyspark.sql import SparkSession, functions as functions
from pyspark.sql.types import StringType, StructField, StructType


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


def normalized_bank(column):
    stripped = functions.regexp_replace(column, "^0+", "")
    return functions.when(stripped == "", "0").otherwise(stripped)


def read_transactions(spark, path):
    header = spark.read.option("header", "false").csv(path).first()
    if header is None or list(header) != RAW_HEADER:
        raise ValueError("Unexpected transaction CSV header or column order")
    schema = StructType([StructField(name, StringType()) for name in TRANSACTION_COLUMNS])
    return spark.read.schema(schema).options(header=True, mode="FAILFAST", enforceSchema=True).csv(path)


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
    reject(transactions, required, "Required transaction fields are empty")
    result = transactions.withColumn("timestamp", functions.to_timestamp("timestamp", "yyyy/MM/dd HH:mm"))
    reject(result, functions.col("timestamp").isNull(), "Invalid timestamp")
    result = result.withColumn("txn_date", functions.to_date("timestamp"))
    reject(result, ~functions.col("txn_date").between("2022-09-01", "2022-09-18"), "Date outside HI-Small range")
    reject(result, ~functions.col("is_laundering").isin("0", "1"), "Invalid label")
    result = result.withColumn("is_laundering", functions.col("is_laundering").cast("int"))
    for name in ["amount_paid", "amount_received"]:
        result = result.withColumn(name, functions.col(name).cast("double"))
        reject(result, functions.col(name).isNull() | functions.isnan(name) |
               (functions.abs(functions.col(name)) == float("inf")) | (functions.col(name) < 0), "Invalid amount")
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
            functions.lit(True).alias("_matched"),
        )
        result = result.join(side_metadata, [bank_key, f"{side}_account"], "left")
        reject(result, functions.col("_matched").isNull(), f"Unmatched {side} account")
        result = result.drop(bank_key, "_matched")
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
        reject(result, functions.col(output_name).isNull() | functions.isnan(output_name) |
               (functions.abs(functions.col(output_name)) == float("inf")), f"Missing FX or nonfinite {output_name}")
        result = result.drop("rate_day", "currency", "usd_rate")
    return result.withColumn("is_self_txn", (functions.col("from_bank") == functions.col("to_bank")) &
                             (functions.col("from_account") == functions.col("to_account"))).withColumn(
        "currency_mismatch", functions.col("payment_currency") != functions.col("receiving_currency"))


def main():
    parser = argparse.ArgumentParser()
    for name in ["transactions", "accounts", "fx", "output"]:
        parser.add_argument(f"--{name}", required=True)
    parser.add_argument("--expected_rows", type=int, required=True)
    args, _ = parser.parse_known_args()
    if args.expected_rows <= 0:
        raise ValueError("expected_rows must be positive")
    spark = SparkSession.builder.appName("is459-curation").getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "false")
    spark.conf.set("spark.sql.shuffle.partitions", "8")
    transactions = read_transactions(spark, args.transactions).cache()
    raw_rows = transactions.count()
    if raw_rows != args.expected_rows:
        raise ValueError(f"Expected {args.expected_rows} raw rows, received {raw_rows}")
    accounts = spark.read.options(header=True, mode="FAILFAST").csv(args.accounts)
    rates = spark.read.options(header=True, mode="FAILFAST").csv(args.fx)
    result = curate(transactions, accounts, rates).cache()
    if result.count() != raw_rows:
        raise ValueError("Account/FX joins changed the row count")
    result.write.mode("errorifexists").partitionBy("txn_date").parquet(args.output)
    written = spark.read.parquet(args.output)
    if written.count() != raw_rows:
        raise ValueError("Written row count differs from input")
    print(f"CURATION_VERIFIED rows={raw_rows} output={args.output}")
    spark.stop()


if __name__ == "__main__":
    main()
