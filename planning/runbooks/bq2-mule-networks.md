# BQ2: mule networks

**Question:** *Which accounts collect money from many sources and quickly pass it
on, or move it in loops, so we know which accounts to restrict and which alerts
belong to the same case?*

**Audience:** mule-account and network investigations team.

This replaces the treasury-corridor BQ2 (team decision, 25 Oct 2026). BQ1 scores
single payments; BQ2 looks at the payment network and finds **groups of
accounts**. One ring = one case. Detection uses only who paid whom, when and how
much. The `is_laundering` label is used afterwards, and only as aggregate rates,
to check the results.

## Method

Code: [`src/bda_pipeline/network.py`](../../src/bda_pipeline/network.py) (reference, pandas)
and [`jobs/network_mules.py`](../../jobs/network_mules.py) (Glue Spark job). Both use
the same constants; `tests/test_network.py` fails if they drift.

| Step | Definition |
|---|---|
| Account | `<bank without leading zeros>:<account>`; self-transfers dropped |
| Account link | All payments from one account to another, merged (count, USD, first/last time) |
| Fan-in / fan-out | Distinct senders into / receivers out of an account |
| Hub | Fan-in ≥ T **and** fan-out ≥ T, with ≤ 100 links in total. T = 5 / 10 / 15 reported; **10** drives rings |
| High-volume account | Passes T but has > 100 links (processor/merchant-like). Listed separately, never in rings or loops |
| Three-account loop | A → B → C → A with payments in that order within 7 days (checked on payment timestamps, any rotation) |
| Two-account back-and-forth | A → B then B → A within 7 days. Reported; weak, so it does not seed rings |
| Ring | Connected components over (hub ↔ ordinary-sized counterparty links) ∪ (loop links); ≥ 3 accounts; `ring_id` = smallest account |
| Ranking | Hubs, then loops, then payments. Labels never change the order |

Spark finds connected components with min-label propagation (no GraphFrames
JAR needed on Glue) and cuts lineage with `localCheckpoint()` each stage.

## Results on full HI-Small (local run, 1 Nov 2026)

5,078,345 payments · 422,734 accounts · 647,939 account links · 591,212
self-transfers excluded · base rate: **1.5%** of accounts touch a labelled
laundering payment. Full numbers: [evidence](../evidence/bq2-mule-network-local-20261101.json).

| Signal | Accounts | Touch laundering | vs base |
|---|---:|---:|---:|
| Fan-out ≥ 10 only | 5,256 | 3.2% | 2.1× |
| Fan-in ≥ 10 only | 2,913 | 10.3% | 6.8× |
| Two-account back-and-forth | 6,205 | 9.3% | 6.2× |
| Three-account loops (232 loops) | 571 | 14.7% | 9.8× |
| **Ring accounts (202 rings)** | **1,110** | **44.3%** | **29.5×** |
| **Mule-sized hubs, T = 10** | **20** | **80.0%** | **53.2×** |

Hub cut-off sensitivity: T = 5 → 566 hubs (13.1%); T = 10 → 20 (80.0%);
T = 15 → 2 (100%). The 15 high-volume accounts at T = 10 are all bank-70
accounts with 500–14,000 counterparties.

Working down the ranked rings: top 10 → 56.9% of 267 accounts; top 25 → 75.2%
of 572; top 50 → 67.3% of 654 touch laundering.

> **Correction to the 25 Oct proposal deck:** "31 of 35 accounts with 10+ senders
> and 10+ receivers are laundering" counts the 15 giant bank-70 accounts, which
> touch *some* laundering payment simply because they have thousands of
> counterparties. The like-for-like mule figure is **16 of 20 (80%)**.

### Caveats

- IBM planted these shapes in synthetic data, so results may look easier than on real data.
- The ranking order was compared with alternatives on these labels before it
  was chosen, so the top-k figures are not an independent test.
- The busy period is about ten days, so all flows are short-term; loops add an
  explicit 7-day ordering check.
- Rings cover 7.7% of laundering-touching accounts: the network view targets
  gather/scatter and cycle typologies; single suspicious payments stay with BQ1.

## Where it runs

```
Glue curate ─▶ ScoreAndAnalyze (parallel)
                ├─ Glue score (BQ1)
                ├─ Glue network  ◀── BQ2 (was: Athena corridor query; skipped on a cache hit)
                └─ SageMaker baseline (+ optional training)
             ─▶ SageMaker export: recompute network in pandas, compare every
                Glue table (exact keys/counts, USD ≤ 1 cent), build dashboard
             ─▶ Lambda publish (requires a passed `network` proof)
```

Output: `s3://…/pipeline/runs/<run>/network/{totals,candidates,pairs,loops,ring_members,ring_edges}/`
plus a root `_SUCCESS`.

### Speed, cost and resilience

- **Stage cache.** Like curate and score, the network stage is reused when an
  identical earlier run was fully verified and published
  ([`stage_cache.py`](../../src/bda_pipeline/stage_cache.py)). Its key is the
  run's input identity + the curated version + the network job version (a hash
  of `network_mules.py`, which holds every threshold). A model change does
  **not** invalidate it, because the network job never reads scores. On a hit,
  Step Functions takes `ReuseNetwork → NetworkReused` and no Glue job starts;
  the export stage still recomputes and checks every table, so a bad cache entry
  cannot be published. A deployment without a `network` stage version (older
  config) always recomputes. Old treasury output cannot be reused: it never had
  a network receipt.
- **One input read.** The Glue job checks the row count first (Parquet footers),
  then validates every row and counts self-transfers in a single aggregation
  that also fills the cache the link aggregation reads.
- **Export check cost.** `network.analyse_network` on full HI-Small: 23.5 s on a
  laptop (10.5 s building links, the rest under 3 s per step). It grows roughly
  linearly with rows, so HI-Medium (~6×) is a few minutes, well inside the
  export job's 60-minute limit.
- **`localCheckpoint()` trade-off.** It keeps Spark plans short and fast, but
  a lost executor fails the job (there is no reliable copy to recompute from).
  That is acceptable because Glue jobs here run on standard capacity with no
  retries and a failed run is rerun deliberately. If the job ever moves to Glue
  Flex (cheaper, but executors can be reclaimed), switch to `checkpoint()` with
  an S3 checkpoint directory.

## Run it locally

Needs Python 3.11, Java 17 and `pip install -e ".[dev]" pyspark==3.5.4`.

```bash
# 1. Curate with the real Glue job in local Spark (HI-Small CSVs + FX CSV)
python jobs/curate_transactions.py --transactions HI-Small_Trans.csv --accounts HI-Small_accounts.csv \
  --fx fx.csv --output data/curated --expected_rows 5078345
# 2. Network job (same script Glue runs)
python jobs/network_mules.py --INPUT data/curated --OUTPUT data/network --EXPECTED_ROWS 5078345
# 3. Reference analysis, verify the Spark tables, build a dashboard preview
python scripts/local/run-network-analysis.py --curated data/curated --glue-output data/network \
  --snapshot dashboard.json --output output/network-preview
python -m http.server -d output/network-preview 8765   # open the Mule networks tab
```

`--snapshot` can be the live release's `dashboard.json` (same 5,078,345 rows).

## Deploying (not done yet)

Nothing on this branch has been deployed. To ship it:

1. Build the new image first (step 3); `scripts/aws/prepare-ingestion.py` pins its digest and now also packages `network_mules.py`.
2. `scripts/aws/update-ingestion.ps1`: Terraform adds the `is459-g3-ingest-network`
   Glue job, its log groups and S3 write scope, and removes the workflow's Athena permissions.
3. Rebuild and push the SageMaker image (`scripts/aws/prepare-sagemaker-container.py`):
   it bakes in `src/bda_pipeline` and `dashboard/`, and the export stage needs the
   new `network.py` and the Mule networks tab. The deployment config pins the
   image digest, so update it to the new digest.
4. Run a demo (1,000 rows) first, then a full HI-Small run. Expect a full run's
   network job to take a few minutes on 2 × G.1X.

The legacy `infra/orchestration` stack still uses the Athena corridor SQL and is unchanged.
