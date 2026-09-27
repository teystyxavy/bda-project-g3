# Big Data Pipeline Plan — IBM AML Transactions

Dataset: [IBM Transactions for Anti-Money Laundering (AML)](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
(local copy under `dataset/`, see [eda.ipynb](../scripts/eda.ipynb) for exploratory analysis).

## 1. Business questions

### BQ1 — Real-time laundering risk scoring (Compliance / Fraud Ops)

**Question:** Which transaction- and behavior-level signals (payment format, currency
mismatch, self-transfers, amount, account velocity) most strongly predict money
laundering, and can we score incoming transactions for risk as they happen?

**Who benefits and why:** AML compliance officers and fraud investigators. They
currently have limited time to review transactions; a risk score lets them triage
the ~0.1% that matter instead of sampling blindly, reduces missed Suspicious
Activity Reports (regulatory/reputational risk), and cuts wasted investigator hours
on false positives.

**Method shape:** per-transaction, supervised/rule-based classification, near-real-time.

### BQ2 — Where is our transaction volume concentrated, and are we too dependent on a few banks or currencies? (Treasury / Risk Management)

**Question:** When we add up transaction volume (converted to a common currency)
between every pair of banks, which handful of bank-to-bank or currency corridors
carry most of the traffic — and is our dependence on those specific corridors
growing over time?

**Who benefits and why:** Treasury and correspondent-banking risk teams (and
business development). Understanding where volume concentrates informs liquidity
buffers, hedging decisions, and which corridors are worth investing
correspondent-banking relationships in.

**Method shape:** aggregate/network analytics, descriptive, batch.

These two are deliberately non-overlapping: BQ1 is predictive and row-level
(compliance use case); BQ2 is descriptive and network/aggregate-level (treasury use
case) — different stakeholders, different analytical technique, different pipeline
branch.

### Data augmentation

To make BQ2's USD-equivalent comparisons meaningful across the dataset's ~15
currencies, augment with daily FX reference rates for Sept 2022 from a free public
source, e.g. the [Frankfurter API](https://www.frankfurter.app/) (ECB-backed
historical rates) or the
[ECB euro reference rates](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html).
Rates are joined in by date + currency during ETL to compute an `amount_usd` field.

## 2. Pipeline architecture

Two layers, not two symmetric branches: a **batch backbone** that is the entire
pipeline for BQ2 and also produces and trains the model BQ1 depends on, and a thin
**streaming layer** that exists only to serve that already-trained model in
near-real-time. Everything is serverless / pay-per-use to fit the $250 budget and
the constraint against Redshift, RDS, and QuickSight.

`[CORE]` = required pipeline, fully answers both business questions on its own.
`[X-FACTOR]` = the only two pieces doing something the core pipeline wouldn't
need — see [§3](#3-x-factor-scope) for why each one qualifies.

### Why the batch backbone exists (it is not just "for visualization")

1. **It is the complete, self-sufficient pipeline for BQ2.** "Which corridors
   concentrate our volume" has no real-time version — it's a batch/aggregate
   question by nature. Ingestion → Glue ETL → Athena → dashboard, start to finish,
   answers BQ2 with nothing else required.
2. **It produces the labeled, feature-engineered training data for BQ1's model.**
   Velocity/behavioral features (rolling counts, distinct counterparties, etc.)
   need the full historical dataset to compute — you cannot derive them from a
   single incoming event. The Glue ETL job builds this table once, in batch.
3. **It is where BQ1's model is actually trained.** The streaming layer below
   never trains anything; it only loads whatever `model.joblib` the batch backbone
   last produced. If the batch backbone didn't run, there would be no model for
   the streaming Lambda to load.

The streaming layer is therefore a *consumer* of the batch backbone's output
(the trained model), not an alternative to it, and it never touches Step Functions
— it runs on its own, independent trigger path (Kinesis → Lambda).

```
┌──────────────────────────────── BATCH BACKBONE  [CORE] ─────────────────────────────────┐
│                                                                                            │
│  Kaggle CSVs ──upload──▶ S3 raw/ (Transactions, Accounts, Patterns)                       │
│  FX API ──Lambda(sched)─▶ S3 raw/fx/                                                       │
│                                    ▼                                                       │
│                     Glue Crawler → Glue Data Catalog (raw)                                 │
│                                    ▼                                                       │
│   [X-FACTOR] Step Functions orchestrates the steps below ───────────────────┐             │
│                                    ▼                                         │             │
│                     Glue ETL Job (PySpark, small DPU):                       │             │
│                      - join accounts ↔ transactions, join FX → amount_usd    │             │
│                      - parse Patterns.txt → attempt_id/pattern_type table    │             │
│                      - engineer: is_self_txn, currency_mismatch, velocity    │             │
│                      - write partitioned Parquet → S3 curated/               │             │
│                                    ▼                                         │             │
│                     Glue Crawler → Glue Data Catalog (curated) ◀─────────────┘             │
│                                    │                                                       │
│                 ┌──────────────────┴──────────────────┐                                   │
│                 ▼                                      ▼                                  │
│     BQ2 — Athena SQL over curated Parquet:      BQ1 — Glue Python Shell / SageMaker        │
│      - volume/USD per bank pair, corridor, day   training job: trains classifier on the    │
│      - concentration index per corridor          curated, labeled (Is Laundering) Parquet  │
│      - payment-format mix trend                       │                                    │
│                 ▼                                      ▼                                   │
│     S3 marts/flow_summary/ (Parquet)          S3 models/model.joblib                       │
│     BQ2's analysis is COMPLETE here —         (an artifact to be loaded, not scored here)   │
│     no streaming needed                                │                                   │
└─────────────────────────────────────────────────────────┼──────────────────────────────────┘
                                                            │ model.joblib (read-only)
                                                            ▼
┌──────────────────────── STREAMING LAYER — BQ1 ONLY  [X-FACTOR] ─────────────────────────┐
│              (independent trigger path — does NOT run inside Step Functions)              │
│                                                                                             │
│  CSV replay producer ──▶ Kinesis Data Stream ──▶ Lambda consumer:                          │
│  (simulates a live feed)  (1 shard, on-demand)     - loads model.joblib from S3             │
│                                                     - reads/updates this account's rolling   │
│                                                       velocity state in DynamoDB             │
│                                                     - scores the transaction                 │
│                                                     - if high-risk → DynamoDB                │
│                                                       flagged_transactions + SNS alert        │
│                                                     - every score → S3 curated/scored/        │
│                                                       (feeds back into Athena for review)     │
└─────────────────────────────────────────────────────────┬───────────────────────────────────┘
                                                            ▼
                              VISUALIZATION (no QuickSight)  [CORE]
                  Lambda/Glue job renders Plotly/Chart.js dashboards from
                  S3 marts/ (BQ2) + S3 curated/scored/ & DynamoDB (BQ1)
                                          ▼
                      S3 static website + CloudFront
              Dashboard 1 (BQ1): risk scores, flagged accounts, alerts-over-time
              Dashboard 2 (BQ2): corridor Sankey/heatmap, bank volume ranking
                                          ▼
              CloudWatch (pipeline health) + SNS (failures & AML alerts)   [CORE]
```

**Without the X-factor pieces:** drop Step Functions and the pipeline still runs,
just triggered by a plain daily cron instead of an orchestrated state machine. Drop
the streaming layer and BQ1's model runs as one more step inside the batch backbone
(a Glue job scoring the curated Parquet once a day, writing straight to
`S3 curated/scored/`) — still a complete, correct answer to BQ1, just next-day
instead of near-real-time. Either way, the batch backbone itself is never optional.

### Stage-by-stage detail

| Stage | Layer | Tier | Service | Purpose | Cost control |
|---|---|---|---|---|---|
| Ingestion (batch) | Batch backbone | Core | S3 (raw zone) | Land Transactions/Accounts/Patterns CSVs | Standard storage, lifecycle → IA after processing |
| Ingestion (FX) | Batch backbone | Core | Lambda + EventBridge schedule | Pull daily FX rates once, land as CSV | Runs once per demo, negligible cost |
| Cataloging | Batch backbone | Core | Glue Crawler + Data Catalog | Schema discovery for Athena/Glue | Crawler runs on trigger, not scheduled continuously |
| Transform | Batch backbone | Core | Glue ETL (PySpark, serverless) | Join, clean, engineer features, write Parquet — builds BQ1's training set *and* BQ2's queryable table | Small G.1X worker count (2–5), partitioned output |
| Orchestration | Batch backbone | **X-factor** | Step Functions + EventBridge | Chain crawl → ETL → BQ1 training / BQ2 Athena → viz → notify | Pay-per-state-transition, pennies at this scale |
| Model training | Batch backbone | Core | Glue Python Shell or SageMaker Training (small/spot instance) | Train laundering classifier (XGBoost) on the curated, labeled Parquet → `model.joblib` in S3 | Runs for minutes, on-demand only, no persistent endpoint |
| Batch flow analytics | Batch backbone | Core | Athena over partitioned Parquet | Corridor/bank concentration aggregates — this alone completes BQ2 | Partition pruning keeps bytes-scanned (and cost) tiny |
| Ingestion (stream) | Streaming (BQ1 only) | **X-factor** | Kinesis Data Streams | Replay CSV rows to simulate a live feed, independent of Step Functions | 1 shard, on-demand mode, run producer only during demo windows |
| Real-time scoring | Streaming (BQ1 only) | **X-factor** | Lambda (Kinesis event source) + DynamoDB | Load `model.joblib` from the batch backbone, score each streamed transaction, flag high-risk | Lambda pay-per-invocation; DynamoDB on-demand capacity |
| Visualization | Shared | Core | Lambda/Glue → static HTML (Plotly/Chart.js) on S3 + CloudFront | Interactive dashboards reading from both layers' outputs | Static hosting, no compute cost after generation |
| Monitoring | Shared | Core | CloudWatch + SNS | Pipeline health, AML alert delivery | Free tier covers this scale |

### Why these choices avoid the banned/expensive services

- **No Redshift** → Athena directly on partitioned S3 Parquet (serverless, pay-per-TB-scanned).
- **No RDS** → DynamoDB on-demand for the small flagged-transactions lookup table.
- **No QuickSight** → self-built static Plotly/Chart.js dashboard on S3+CloudFront, which
  also doubles as the "advanced interactive visualization" X-factor since it can do things
  (Sankey flow diagrams, client-side drill-down) a default BI tool wouldn't in the labs.
- **No EMR persistent cluster** → Glue serverless ETL, billed per DPU-minute, nothing left running.

### Budget discipline

- Set an **AWS Budget** with alerts at $150 / $200 / $230 against the $250 cap.
- Demo end-to-end on **HI-Small** (475MB transactions, 5M rows) — the same
  Glue/Athena code scales to Medium/Large by just repointing the S3 prefix; call
  this out in the report as a scaling discussion rather than actually burning
  budget running Large.
- Keep the Kinesis producer running only for the live-demo window, not continuously.

## 3. X-factor scope

**Baseline pipeline, for reference:** the batch backbone alone — S3 (raw) → Glue
Crawler → Glue ETL → S3 (curated Parquet) → [Athena for BQ2 / Glue training job +
nightly batch scoring for BQ1] → dashboard on S3/CloudFront, run manually or on a
plain daily trigger. That alone is a complete, gradeable pipeline that fully
answers both business questions — zero X-factor in it. Nothing about BQ2 changes
if the X-factor pieces below are removed; BQ1 just becomes next-day instead of
real-time.

The brief warns that complexity alone doesn't count — recommend picking **two**,
done well, rather than stacking every option. These are the only two pieces of the
plan doing something the baseline pipeline wouldn't need, and both live entirely
inside or alongside the batch backbone — neither one replaces it:

1. **Step Functions orchestration** (inside the batch backbone). Without it,
   running the pipeline means someone clicking through Glue Crawler → Glue Job →
   Athena query → training job → viz script by hand (or a plain cron). Step
   Functions chains those steps into one automated state machine with
   retries/error branching, triggered by an EventBridge rule — this is the
   "pipeline automation or orchestration" bullet, directly.
2. **Streaming for BQ1** (a separate layer, Kinesis → Lambda → DynamoDB, sitting
   downstream of the batch backbone). The baseline scores transactions once a day
   in a batch job, so a compliance officer would only learn about a suspicious
   transaction the next day. The streaming layer loads the model the batch
   backbone already trained and scores each transaction within seconds of it
   happening — the "streaming / near-real-time processing" bullet. This only
   applies to BQ1: BQ2 is inherently a batch/aggregate question, so forcing
   streaming onto it would be complexity for no reason, and the streaming layer
   has no training logic of its own to begin with.

If you strip these two out, the pipeline still fully answers both business
questions — that's what makes them X-factor rather than core plumbing.

**ML/AI (optional):** start with a transparent rule-based risk score (weighted sum
of the flags the EDA already found — self-transaction, currency mismatch, amount
outliers, risky payment formats). Treat a trained classifier (XGBoost) as a stretch
goal only if time/budget allow, since adding ML isn't credited unless it
measurably improves BQ1's answer (e.g., better precision/recall@k than the
rule-based baseline).
