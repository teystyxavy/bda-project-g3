"""Glue 5 BQ2 mule-network job: hubs, loops and rings from curated Parquet.

Where this sits in the pipeline
-------------------------------
AWS Glue PySpark job ``is459-g3-ingest-network`` (``infra/ingestion``). It runs
in the Step Functions ``ScoreAndAnalyze`` parallel state, next to BQ1 scoring
and the SageMaker baseline check, and replaces the old Athena corridor query::

    S3 curated Parquet (from jobs/curate_transactions.py)
      -> this job
      -> S3 pipeline/runs/<run>/network/<table>/ Parquet for every table in
         bda_pipeline.network.TABLES, plus a root _SUCCESS marker

``ingest_compute.export_stage`` then recomputes the same tables with
``bda_pipeline.network`` in SageMaker Processing and refuses to publish unless
both routes agree exactly (USD within 1 cent).

The method and every threshold are documented in ``src/bda_pipeline/network.py``;
the constants below must stay equal to that module's (checked by
``tests/test_network.py``). The script is self-contained like the curate job, so
it needs no extra Python packages on Glue.

Arguments: ``--INPUT`` (curated Parquet URI), ``--OUTPUT`` (fresh network
prefix), ``--EXPECTED_ROWS`` and optional ``--SHUFFLE_PARTITIONS``.
"""
import argparse
import json
from functools import reduce
from time import perf_counter

from pyspark import StorageLevel
from pyspark.sql import SparkSession, functions as F

HUB_THRESHOLDS = (5, 10, 15)
HEADLINE_THRESHOLD = 10
MAX_LINKS = 100
LOOP_WINDOW_DAYS = 7
MIN_RING_ACCOUNTS = 3
MAX_ITERATIONS = 200
TABLE_NAMES = ['totals', 'candidates', 'pairs', 'loops', 'ring_members', 'ring_edges']


def normalized_bank(column):
    """Bank code without leading zeros ('' -> '0'); same rule as the curate job and ``analytics.bank_key``."""
    stripped = F.regexp_replace(column, "^0+", "")
    return F.when(stripped == "", "0").otherwise(stripped)


def account_key(bank, account):
    return F.concat(normalized_bank(F.col(bank)), F.lit(":"), F.col(account))


def within_window(later, earlier):
    """``earlier <= later <= earlier + LOOP_WINDOW_DAYS`` on timestamps (inclusive at both ends)."""
    return (F.col(later) >= F.col(earlier)) & (F.col(later) <= F.col(earlier) + F.expr(f"INTERVAL {LOOP_WINDOW_DAYS} DAYS"))


def build_links(curated):
    """Directed account links and the self-transfer count (self-transfers are dropped)."""
    tx = curated.select(account_key("from_bank", "from_account").alias("src"),
                        account_key("to_bank", "to_account").alias("dst"),
                        F.col("timestamp").alias("ts"), F.col("amount_usd").cast("double").alias("usd"),
                        F.col("is_laundering").cast("long").alias("lab"))
    bad = tx.filter(F.col("src").isNull() | F.col("dst").isNull() | F.col("ts").isNull() | F.col("usd").isNull()
                    | F.col("lab").isNull() | ~F.col("lab").isin(0, 1) | (F.col("usd") < 0)
                    | F.isnan("usd") | (F.col("usd") == float("inf"))).limit(1).count()
    if bad:
        raise ValueError("Missing or invalid network input value")
    tx = tx.persist(StorageLevel.MEMORY_AND_DISK)
    self_transfers = tx.filter(F.col("src") == F.col("dst")).count()
    tx = tx.filter(F.col("src") != F.col("dst"))
    links = tx.groupBy("src", "dst").agg(F.count(F.lit(1)).alias("txns"), F.sum("usd").alias("usd"),
                                         F.min("ts").alias("first_ts"), F.max("ts").alias("last_ts"),
                                         F.sum("lab").alias("laundering_txns"))
    return tx, links.localCheckpoint(), self_transfers


def account_table(links):
    """Fan-in/fan-out, payments, USD and whether any link carried a labelled payment, per account."""
    out = links.groupBy(F.col("src").alias("account")).agg(
        F.count(F.lit(1)).alias("fan_out"), F.sum("txns").alias("out_txns"), F.sum("usd").alias("out_usd"),
        F.sum("laundering_txns").alias("out_lab"))
    inn = links.groupBy(F.col("dst").alias("account")).agg(
        F.count(F.lit(1)).alias("fan_in"), F.sum("txns").alias("in_txns"), F.sum("usd").alias("in_usd"),
        F.sum("laundering_txns").alias("in_lab"))
    accounts = out.join(inn, "account", "full_outer").fillna(0, ["fan_out", "out_txns", "out_lab", "fan_in", "in_txns", "in_lab"])
    accounts = accounts.fillna(0.0, ["out_usd", "in_usd"])
    accounts = accounts.withColumn("links", F.col("fan_in") + F.col("fan_out"))
    accounts = accounts.withColumn("touches_laundering", (F.col("out_lab") + F.col("in_lab")) > 0)
    return accounts.drop("out_lab", "in_lab").localCheckpoint()


def candidate_loops(ordinary_links):
    """Directed a -> b -> c -> a cycles among ordinary accounts, canonical rotation (a smallest)."""
    e1 = ordinary_links.select(F.col("src").alias("a"), F.col("dst").alias("b"))
    e2 = ordinary_links.select(F.col("src").alias("b"), F.col("dst").alias("c"))
    e3 = ordinary_links.select(F.col("src").alias("c"), F.col("dst").alias("a"))
    paths = e1.join(e2, "b").filter(F.col("a") != F.col("c"))
    cycles = paths.join(e3, ["c", "a"])
    return cycles.filter((F.col("a") < F.col("b")) & (F.col("a") < F.col("c"))).select("a", "b", "c").distinct()


def timed_pairs(times):
    """Two-account back-and-forth: a payment one way, then one back within the window."""
    x = times.select(F.col("src").alias("p"), F.col("dst").alias("q"), F.col("ts").alias("t1"))
    y = times.select(F.col("src").alias("q"), F.col("dst").alias("p"), F.col("ts").alias("t2"))
    matched = x.join(y, ["p", "q"]).filter(within_window("t2", "t1"))
    return matched.select(F.least("p", "q").alias("a"), F.greatest("p", "q").alias("b")).distinct()


def timed_loops(candidates, times):
    """Exact check: some rotation of each cycle has payments in order within the window."""
    rotations = reduce(lambda left, right: left.unionByName(right), [
        candidates.select("a", "b", "c", F.col(p).alias("p"), F.col(q).alias("q"), F.col(r).alias("r"))
        for p, q, r in [("a", "b", "c"), ("b", "c", "a"), ("c", "a", "b")]])
    leg1 = times.select(F.col("src").alias("p"), F.col("dst").alias("q"), F.col("ts").alias("t1"))
    leg2 = times.select(F.col("src").alias("q"), F.col("dst").alias("r"), F.col("ts").alias("t2"))
    leg3 = times.select(F.col("src").alias("r"), F.col("dst").alias("p"), F.col("ts").alias("t3"))
    joined = rotations.join(leg1, ["p", "q"]).join(leg2, ["q", "r"]).filter(F.col("t2") >= F.col("t1"))
    joined = joined.join(leg3, ["r", "p"]).filter((F.col("t3") >= F.col("t2")) & within_window("t3", "t1"))
    return joined.select("a", "b", "c").distinct()


def connected_components(edges, spark):
    """Undirected components by min-label propagation; returns (account, ring_id = smallest account).

    Each round every account takes the smallest label among itself and its
    neighbours, so labels spread one hop per round; it stops when nothing
    changes (at most the graph diameter + 1 rounds). Checkpointing each round
    keeps the Spark plan short. (``localCheckpoint`` is used throughout the job
    to cut lineage; the job has no retries, so a lost executor fails the run.)
    """
    both = edges.select(F.col("src").alias("u"), F.col("dst").alias("v")).unionByName(
        edges.select(F.col("dst").alias("u"), F.col("src").alias("v"))).distinct().localCheckpoint()
    labels = both.select(F.col("u").alias("account")).distinct().withColumn("ring_id", F.col("account")).localCheckpoint()
    for iteration in range(MAX_ITERATIONS):
        offered = both.join(labels.withColumnRenamed("account", "v"), "v").groupBy(F.col("u").alias("account")).agg(
            F.min("ring_id").alias("offered"))
        updated = labels.join(offered, "account", "left").select(
            "account", F.least("ring_id", F.coalesce("offered", "ring_id")).alias("ring_id"), F.col("ring_id").alias("previous"))
        updated = updated.localCheckpoint()
        changed = updated.filter(F.col("ring_id") != F.col("previous")).count()
        labels = updated.drop("previous")
        if changed == 0:
            print(json.dumps({"phase": "components", "rounds": iteration + 1}), flush=True)
            return labels
    raise ValueError("Connected components did not converge")


def analyse(curated, spark, expected_rows):
    """Return ``{table name: DataFrame}`` matching ``bda_pipeline.network.analyse_network``."""
    tx, links, self_transfers = build_links(curated)
    accounts = account_table(links)
    rows = curated.count()
    if rows != expected_rows:
        raise ValueError(f"Expected {expected_rows} curated rows, received {rows}")
    totals = spark.createDataFrame([(rows, self_transfers, accounts.count(), links.count(),
                                     accounts.filter("touches_laundering").count())],
                                   "rows long, self_transfers long, accounts long, links long, laundering_accounts long")

    low = min(HUB_THRESHOLDS)
    candidates = accounts.filter((F.col("fan_in") >= low) & (F.col("fan_out") >= low)).select(
        "account", "fan_in", "fan_out", "in_txns", "out_txns", "in_usd", "out_usd").localCheckpoint()

    ordinary = accounts.filter(F.col("links") <= MAX_LINKS).select("account")
    ordinary_links = (links.join(ordinary.withColumnRenamed("account", "src"), "src", "left_semi")
                      .join(ordinary.withColumnRenamed("account", "dst"), "dst", "left_semi")
                      .localCheckpoint())

    reverse = ordinary_links.select(F.col("src").alias("dst"), F.col("dst").alias("src"))
    pair_edges = ordinary_links.select("src", "dst").join(reverse, ["src", "dst"], "left_semi")
    loop_candidates = candidate_loops(ordinary_links).localCheckpoint()
    loop_edges = reduce(lambda left, right: left.unionByName(right), [
        loop_candidates.select(F.col(s).alias("src"), F.col(d).alias("dst")) for s, d in [("a", "b"), ("b", "c"), ("c", "a")]])
    needed = pair_edges.unionByName(loop_edges).distinct()
    times = tx.select("src", "dst", "ts").join(needed, ["src", "dst"], "left_semi").localCheckpoint()
    pairs = timed_pairs(times).localCheckpoint()
    loops = timed_loops(loop_candidates, times).localCheckpoint()

    hubs = accounts.filter((F.col("fan_in") >= HEADLINE_THRESHOLD) & (F.col("fan_out") >= HEADLINE_THRESHOLD)
                           & (F.col("links") <= MAX_LINKS)).select("account").localCheckpoint()
    hub_links = (ordinary_links.join(hubs.withColumnRenamed("account", "src"), "src", "left_semi").select("src", "dst")
                 .unionByName(ordinary_links.join(hubs.withColumnRenamed("account", "dst"), "dst", "left_semi").select("src", "dst")))
    loop_links = reduce(lambda left, right: left.unionByName(right), [
        loops.select(F.col(s).alias("src"), F.col(d).alias("dst")) for s, d in [("a", "b"), ("b", "c"), ("c", "a")]])
    ring_links = hub_links.unionByName(loop_links).distinct().join(links, ["src", "dst"]).localCheckpoint()

    labels = connected_components(ring_links, spark)
    sizes = labels.groupBy("ring_id").agg(F.count(F.lit(1)).alias("size"))
    kept = labels.join(sizes, "ring_id").filter(F.col("size") >= MIN_RING_ACCOUNTS).select("account", "ring_id").localCheckpoint()
    loop_members = reduce(lambda left, right: left.unionByName(right),
                          [loops.select(F.col(name).alias("account")) for name in ["a", "b", "c"]]).distinct()
    members = (kept.join(hubs.withColumn("is_hub", F.lit(True)), "account", "left")
               .join(loop_members.withColumn("is_loop", F.lit(True)), "account", "left")
               .withColumn("role", F.when(F.col("is_hub"), "hub").when(F.col("is_loop"), "loop").otherwise("counterparty"))
               .join(accounts.select("account", "fan_in", "fan_out", "in_usd", "out_usd"), "account")
               .select("account", "ring_id", "role", "fan_in", "fan_out", "in_usd", "out_usd").localCheckpoint())
    ring_edges = ring_links.join(kept.withColumnRenamed("account", "src"), "src").select(
        "src", "dst", "ring_id", "txns", "usd", "first_ts", "last_ts", "laundering_txns")
    return dict(totals=totals, candidates=candidates, pairs=pairs, loops=loops, ring_members=members, ring_edges=ring_edges)


def write_success(spark, output):
    """Root ``_SUCCESS`` marker so the control Lambda can confirm the whole output exists."""
    jvm = spark.sparkContext._jvm
    path = jvm.org.apache.hadoop.fs.Path(output.rstrip("/") + "/_SUCCESS")
    path.getFileSystem(spark.sparkContext._jsc.hadoopConfiguration()).create(path, False).close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--INPUT", required=True)
    parser.add_argument("--OUTPUT", required=True)
    parser.add_argument("--EXPECTED_ROWS", type=int, required=True)
    parser.add_argument("--SHUFFLE_PARTITIONS", type=int, default=16)
    args, _ = parser.parse_known_args()
    if args.EXPECTED_ROWS <= 0 or not 1 <= args.SHUFFLE_PARTITIONS <= 128:
        raise ValueError("Invalid job arguments")
    spark = SparkSession.builder.appName("is459-network").getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    spark.conf.set("spark.sql.ansi.enabled", "false")
    spark.conf.set("spark.sql.shuffle.partitions", str(args.SHUFFLE_PARTITIONS))
    started = perf_counter()
    curated = spark.read.parquet(args.INPUT).select(
        "timestamp", "from_bank", "from_account", "to_bank", "to_account", "amount_usd", "is_laundering")
    tables = analyse(curated, spark, args.EXPECTED_ROWS)
    output = args.OUTPUT.rstrip("/")
    counts = {}
    for name in TABLE_NAMES:
        # Every table is small (thousands of rows), so one file each is enough.
        tables[name].coalesce(1).write.mode("errorifexists").parquet(f"{output}/{name}")
        counts[name] = spark.read.parquet(f"{output}/{name}").count()
    write_success(spark, output)
    print(json.dumps({"phase": "network_write", "tables": counts, "seconds": round(perf_counter() - started, 3)}), flush=True)
    print(f"NETWORK_VERIFIED rows={args.EXPECTED_ROWS} output={output}")
    spark.stop()


if __name__ == "__main__":
    main()
