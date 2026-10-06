# Big Data Pipeline Plan — IBM AML Transactions

**Optimisation update, 6 October 2026:** the full batch optimisations are
implemented and deployed. The fresh 5,078,345-row rebuild took **20m55s**, versus
24m59s previously (**16.3% faster**, one observed run per configuration). Both
business views, all native scores and corridor reconciliation passed unchanged.
The stage-cache run with SageMaker candidate training took **18m02s**, reused both
curated/scored stages and started no Glue jobs. An unchanged ingestion returned its
verified release in **33.85s** without starting the batch. Training never promotes
the candidate automatically. The current dashboard selects
`opt-train-cache-20261006-aws1`, Kaggle version 8.
See the [implementation](runbooks/pipeline-optimisation-implementation.md) and
[measured evidence](evidence/pipeline-optimisation-result-20261006.json).
The existing `scripts/ingest.ps1` entry point runs AWS acquisition through publication;
`-Train` adds candidate training and `-Rerun` bypasses caches.

AWS acquisition revision: Kaggle and historical FX retrieval now have a dedicated
Lambda container and a Standard Step Functions wrapper before the verified batch.
The nine-resource addition is deployed; smoke and full source-to-dashboard runs
passed reconciliation and public hashes. Full acquisition took 53 seconds and
the complete run took about 25 minutes; see [deployment evidence](evidence/cloud-ingestion-deployment-result-20261006.json)
and [review](archive/reviews/cloud-ingestion-review.md). The previous verified path remains available with
`-LocalAcquisition`; its recorded results remain valid.

Implementation update, 6 October 2026: command-driven Kaggle/FX acquisition,
per-run cloud orchestration, verification/publication and optional SageMaker candidate
training are implemented and their 33 Terraform additions are deployed.
Both the 1,000-row AWS smoke and full 5,078,345-row training/publication run succeeded. See [the current review](archive/reviews/ingestion-implementation-review-20261006.md)
and [runbook](runbooks/ingestion-runbook.md); earlier proposed architecture/cost discussion
below is historical planning and does not establish live deployment success.

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

## frrom the discussion: use the score to rank the laundering attempts in order of priority.

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

## benefits the risk teams

### Data augmentation

To make BQ2's USD-equivalent comparisons meaningful across the dataset's 15
currencies, augment with daily historical rates for Sept 2022. The implemented
local ingestion uses the [Frankfurter API](https://frankfurter.dev/) for its
covered central-bank-backed fiat rates, with Yahoo Finance daily history for
Bitcoin, Ruble, and Saudi Riyal because Frankfurter has no queryable Sept 2022
records for those pairs. Each row records its source and actual observation date;
weekends use the latest prior observation. Rates are joined by date + currency
during ETL to compute an `amount_usd` field.

## 2. Pipeline architecture

**Implemented status, 6 October 2026:** AWS curation, frozen-model scoring,
Athena treasury queries and public static hosting are verified. Model training,
explanation reconstruction, output reconciliation and dashboard export run
locally. The first Step Functions workflow now coordinates guarded scoring and
treasury queries over verified curated data; its approved full run reconciled
both outputs. Scheduling is absent; the bounded streaming replay is verified and its consumer is disabled. The diagram
below records the original target architecture, including future cloud training
and broader orchestration; use §4.6 and the deployment status for actual scope.
The current editable implementation diagrams are
[core batch paths](architecture/aml-pipeline-architecture.drawio) and
[optional enhancements](architecture/aml-pipeline-aws-icons.drawio), with companion guides
and locally reviewed PNGs in `architecture-exports/`.

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

| Stage                | Layer                | Tier               | Service                                                         | Purpose                                                                                                 | Cost control                                                   |
| -------------------- | -------------------- | ------------------ | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| Ingestion (batch)    | Batch backbone       | Core               | S3 (raw zone)                                                   | Land Transactions/Accounts/Patterns CSVs                                                                | Standard storage, lifecycle → IA after processing             |
| Ingestion (FX)       | Batch backbone       | Core               | Lambda + EventBridge schedule                                   | Pull historical Frankfurter/Yahoo rates once, retain source metadata, land as CSV                       | Runs once per demo, negligible cost                            |
| Cataloging           | Batch backbone       | Core               | Glue Crawler + Data Catalog                                     | Schema discovery for Athena/Glue                                                                        | Crawler runs on trigger, not scheduled continuously            |
| Transform            | Batch backbone       | Core               | Glue ETL (PySpark, serverless)                                  | Join, clean, engineer features, write Parquet — builds BQ1's training set*and* BQ2's queryable table | Small G.1X worker count (2–5), partitioned output             |
| Orchestration        | Batch backbone       | **X-factor** | Step Functions; optional absent EventBridge Scheduler | Verify contracts/fresh output → parallel frozen BQ1 scoring / BQ2 Athena → marker check; catchable failure notification | Bounded compute, no automatic compute retry or historical daily replay |
| Model training       | Batch backbone       | Core               | Glue Python Shell or SageMaker Training (small/spot instance)   | Train laundering classifier (XGBoost) on the curated, labeled Parquet →`model.joblib` in S3          | Runs for minutes, on-demand only, no persistent endpoint       |
| Batch flow analytics | Batch backbone       | Core               | Athena over partitioned Parquet                                 | Corridor/bank concentration aggregates — this alone completes BQ2                                      | Partition pruning keeps bytes-scanned (and cost) tiny          |
| Ingestion (stream)   | Streaming (BQ1 only) | **X-factor** | Kinesis Data Streams                                            | Replay CSV rows to simulate a live feed, independent of Step Functions                                  | 1 provisioned shard; producer only during approved demo windows |
| Real-time scoring    | Streaming (BQ1 only) | **X-factor** | Lambda (Kinesis event source) + DynamoDB                        | Load verified frozen model artifacts, recompute replay history, score each transaction           | Lambda pay-per-invocation; DynamoDB on-demand capacity         |
| Visualization        | Shared               | Core               | Lambda/Glue → static HTML (Plotly/Chart.js) on S3 + CloudFront | Interactive dashboards reading from both layers' outputs                                                | Static hosting, no compute cost after generation               |
| Monitoring           | Shared               | Core               | CloudWatch + SNS                                                | Pipeline health, AML alert delivery                                                                     | Free tier covers this scale                                    |

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
   frozen scoring and treasury queries require separate manual starts. The
   delivered workflow verifies their input/job contracts, rejects reused output,
   coordinates both tasks, checks the real success marker and handles catchable
   failures. It retries bounded reads, preserves failed compute output and runs
   only after a reviewed approval. EventBridge Scheduler is an optional one-off
   extension and is absent. Broader ingestion/training/publication automation
   remains future scope; this delivers the orchestration X-factor directly.
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

## 4. Implementation details (resolving the gaps above)

### 4.1 Raw schema quirks and join keys

- **`{PREFIX}-{SIZE}_Trans.csv` has two columns literally both named `Account`**
  (the 3rd column is *From* Account, the 5th is *To* Account — confirmed by
  inspecting the header row). A Glue Crawler/pandas `read_csv` will silently
  dedupe or mis-infer this. The Glue ETL job must read by **column position**,
  not by inferred header name, and rename explicitly to `from_account` /
  `to_account`.
- **`{PREFIX}-{SIZE}_accounts.csv` columns:** `Bank Name, Bank ID, Account Number,
  Entity ID, Entity Name`.
- **Trans ↔ Accounts join key is composite, not just `Account Number`.**
  Verified on `HI-Small_accounts.csv`: of 518,581 account rows, `Account Number`
  alone has only 518,573 distinct values (8 collisions across different banks),
  while `(Bank ID, Account Number)` is fully unique (518,581/518,581). Join on
  `(from_bank = Bank ID, from_account = Account Number)` and the same for the
  `to_*` side — joining on `Account Number` alone will mis-attribute ~8 accounts
  even at `Small` scale, and that error scales with dataset size.
- **Trans ↔ Patterns join key: no shared ID exists.** `Patterns.txt` repeats the
  same fields as `Trans.csv` with no transaction ID, so join on the full natural
  key: `(Timestamp, From Bank, From Account, To Bank, To Account, Amount
  Received, Receiving Currency, Amount Paid, Payment Currency, Payment Format)`.
  Verified zero duplicate natural keys among `HI-Small`'s `Is Laundering=1` rows,
  so this is safe to join directly at that scale. As a safeguard for
  `Medium`/`Large` (where collisions become more likely), add
  `ROW_NUMBER() OVER (PARTITION BY <natural key> ORDER BY source_row_index)` on
  both sides and join on key + row number.
- **`Patterns.txt` is not exhaustive of `Is Laundering=1` rows — document this,
  don't "fix" it.** Verified on `HI-Small`: `Trans.csv` has 5,177 rows with
  `Is Laundering=1`, but `Patterns.txt` has only 3,209 data rows. ~38% of labeled
  laundering transactions belong to no named attempt. The curated table's
  `attempt_id`/`pattern_type` columns will be `NULL` for those rows by design —
  `is_laundering` (from `Trans.csv`) remains the ground-truth label for BQ1
  training regardless of whether a pattern match exists.

### 4.2 Curated table schema

`s3://.../curated/transactions/`, Parquet, **partitioned by `txn_date`** (the date
part of `Timestamp`). Rejected partitioning by bank as well: bank cardinality is
in the thousands, which would fragment the table into too many small files for
Athena to scan efficiently — column-level stats on `from_bank`/`to_bank` inside
each day's partition already give adequate pruning for BQ2's bank-pair queries.

| Column | Type | Notes |
| --- | --- | --- |
| `transaction_id` | string | local analysis uses `HI-Small:{source_row_index}`; stable for this unchanged source file, not a universal transaction ID |
| `timestamp` | timestamp | parsed from raw `Timestamp` |
| `txn_date` | date | **partition column** |
| `from_bank`, `to_bank` | string | raw `Bank ID`-style fields, kept as string (values have leading zeros, e.g. `"020"`) |
| `from_account`, `to_account` | string | see §4.1 column-position fix |
| `amount_received`, `amount_paid` | double | |
| `receiving_currency`, `payment_currency` | string | |
| `amount_usd` | double | see formula below |
| `payment_format` | string | |
| `is_laundering` | int | ground truth, from `Trans.csv` |
| `is_self_txn` | boolean | see §4.3 |
| `currency_mismatch` | boolean | see §4.3 |
| `from_entity_id`, `from_entity_type`, `to_entity_id`, `to_entity_type` | string | joined from `accounts.csv` via composite key |
| `attempt_id`, `pattern_type` | string, nullable | joined from `Patterns.txt`; `NULL` is expected (§4.1) |
| `txn_count_1h`, `txn_count_24h`, `txn_count_7d` | bigint | velocity, see §4.3 |
| `distinct_counterparties_24h`, `distinct_counterparties_7d` | bigint | fan-out signal |
| `is_new_account` | boolean | first transaction seen for this `from_account` |

### 4.3 Feature formulas (concrete, not just names)

- `is_self_txn` = `from_bank == to_bank AND from_account == to_account`
- `currency_mismatch` = `receiving_currency != payment_currency`
- `amount_usd` = `amount_paid` if `payment_currency == "US Dollar"`, else
  `amount_paid * fx_rate(payment_currency → USD, date = txn_date)` (rate from the
  FX ingestion job, §2)
- Velocity features are computed **per normalized `(from_bank, from_account)`**,
  over `[timestamp - window, timestamp)`. Exclude all transactions sharing the
  current timestamp. Spark now partitions by normalized sender, sorts by epoch
  seconds/source index, then streams queues and counterparty counters. Each
  timestamp reuses a snapshot taken before any of its peers are added. This
  preserves exact range-window semantics while avoiding repeated distinct-set
  construction for busy senders. Local analysis also uses per-sender queues:
  - `txn_count_1h` / `txn_count_24h` / `txn_count_7d`
  - `distinct_counterparties_24h` / `_7d` (count distinct normalized `(to_bank, to_account)`)
  - `is_new_account` = `to_account` not seen for this `from_account` in the prior
    30 days
- The dataset spans **2022-09-01 to 2022-09-18** (verified on `HI-Small`), so 1h/24h/7d
  windows have incomplete history near the start; first-week values have a cold-start limitation.
- Local BQ1 scoring uses `amount_usd_asof` with the previous calendar day's FX
  reference (already carried backward over weekends). BQ2 retains same-day
  `amount_usd` as a historical valuation. Same-day daily closes must not be used
  to simulate intraday scoring. Dataset timestamps lack a verified timezone, so
  this is a conservative calendar-day convention, not a verified publication-time feed.
- Fit payment-format P99 thresholds on September 1–6 only. Validation is
  September 7–8 and test September 9–10. Report September 11–18 separately as a
  sparse, label-enriched tail. Unseen formats receive no amount
  points. Rules are hypotheses; earlier whole-dataset EDA means these metrics
  are exploratory, not an untouched confirmatory holdout.
- **Rule-based risk score** (0–100) — revised against the actual EDA numbers
  from `HI-Small` (5,078,345 transactions; see `eda.ipynb` §4 Summary), which
  **overturned the original placeholder assumptions below**:
  - **`payment_format == "ACH"` → `+50`.** By far the strongest signal found:
    ACH's laundering rate is 0.746%, ~7.3x the 0.102% base rate. Every other
    format (`Bitcoin` 0.038%, `Cash` 0.022%, `Cheque` 0.017%, `Credit Card`
    0.016%, `Reinvestment`/`Wire` 0%) is at or below baseline and contributes
    nothing.
  - **`is_self_txn` → dropped from the score (was `+25`).** Measured laundering
    rate for self-transactions is 0.0037%, *lower* than cross-account transfers
    (0.115%) — the opposite of the assumed direction. Kept as a feature for
    model training/explainability, but not rewarded in the rule-based score.
  - **`currency_mismatch` → dropped from the score (was `+15`).** Measured
    laundering rate among currency-mismatched transactions is 0% (0 of 5,177
    labeled laundering transactions involve a mismatch). Same treatment as
    `is_self_txn` above.
  - `+20` if `amount_usd` > P99 of the amount distribution **for that
    `payment_format`** (unchanged, but now confirmed necessary: raw
    `Amount Paid` has mean ~$4.5M and max >$1 trillion, almost entirely a
    currency-denomination artifact — e.g. Yen/Rupiah face values — not real
    outliers, so this must run on FX-normalized `amount_usd`, never the raw
    columns).
  - `+30` if `txn_count_1h > 10` OR `distinct_counterparties_24h > 15` (chosen
    just under the `Max 16-degree Fan-Out` ceiling stated in `HI-Small_Patterns.txt`'s
    own attempt header). **Still unvalidated by EDA** — the notebook only
    profiles transaction-level fields, not per-account rolling behavior, so
    this weight is a hypothesis to confirm once the Glue ETL actually computes
    `txn_count_1h`/`distinct_counterparties_24h`, not an EDA-measured number
    like the two above.
  - **Flag threshold:** score ≥ 60 → write to DynamoDB `flagged_transactions` +
    SNS alert. With these revised weights, ACH alone (50) no longer crosses the
    threshold — it needs one more corroborating signal (amount outlier or
    fan-out), which is the intended behavior given ACH's 0.75% hit rate is
    still far from a near-certainty on its own. Used identically by the batch
    scorer and the streaming Lambda so the two layers agree before any ML model
    replaces the rule-based score.
  - **Caveat for the report:** the self-transaction/currency-mismatch reversal
    is counterintuitive relative to general AML domain knowledge (real-world
    layering schemes do lean on self-transfers and cross-currency mixing) — call
    this out explicitly as a property of this synthetic generator rather than a
    general AML finding, since it's worth a sentence in the writeup either way.

### 4.4 Model training specifics (BQ1)

**SageMaker implementation, 6 October 2026:** `sagemaker/` and `infra/sagemaker/`
add an on-demand custom CPU training container, bounded job launcher and isolated
execution-role/ECR infrastructure. They reuse the existing chronological trainer
and native `.ubj`/JSON format. See [the runbook](../sagemaker/README.md).
This is locally tested source, not deployed cloud training; the current dashboard,
Glue scorer and streaming artifacts continue to use the verified frozen model.
Training is an explicit candidate-generation step, separate from normal ingestion.
No automatic model pointer or dashboard promotion has been introduced.

- **Split by `txn_date`, not randomly:** train on days 1–6, validate on 7–8,
  test on 9–10, and report the sparse days 11–18 separately. The full local scan
  found only 1,108 rows after September 10, of which 655 were positives; the
  original days 16–18 test period was unsuitable. A random row split would leak behavioral/velocity features
  (which aggregate trailing history) across train/test and would also split
  individual multi-hop laundering attempts across both sets.
- **Class imbalance:** ~0.1% positive rate (5,177/5M on `HI-Small`). Use
  XGBoost's `scale_pos_weight = neg_count/pos_count` rather than
  oversampling/SMOTE for this initial ranking experiment. Weighted logistic
  outputs are **not calibrated probabilities**; do not present them as the
  chance of laundering or apply an operational 0.5 threshold. BQ1 ranks
  transactions for a fixed investigation capacity. Calibration would require
  a separate chronological validation design before probability-based decisions.
  See [XGBoost's imbalance guidance](https://xgboost.readthedocs.io/en/stable/tutorials/param_tuning.html).
- **Metrics:** primary = PR-AUC, not ROC-AUC (ROC-AUC is misleadingly high under
  this class imbalance). Report `precision@100` / `recall@100` as the headline
  numbers, since they map directly to "how many of the top 100 ranked
  transactions an investigator reviews are actually laundering."
- **Model versioning:** write each training run to
  `s3://.../models/{run_id}/model.joblib` (`run_id` = Step Functions execution
  ID), and maintain a separate `s3://.../models/current_model_pointer.json`
  (`{run_id, metrics}`) that the streaming Lambda reads. Promoting or rolling
  back a model means updating the pointer file, not redeploying Lambda code.

Local comparison is now implemented by `scripts/compare-local-model.py` using
native XGBoost `.ubj` models plus a separate JSON feature vocabulary/schema.
This replaces the planned local pickle/joblib format for portable, explicit
model loading; future cloud/streaming code must consume the same saved schema.
The validation-selected depth-5 model achieves 44/100 positives in the main
test queue versus 0/100 for rules. See `planning/archive/reviews/local-model-validation.md` for
the frozen selection, reproducibility and exploratory-data limitations. No cloud
model pointer or production promotion exists yet.

### 4.5 Streaming layer specifics

The bounded implementation has fourteen deployed resources. **The single replay
passed all 884 cloud result comparisons and 32 exact sender-history checks;
consumption is Disabled and the retained stack has no drift.** See the
[completed streaming result](archive/reviews/streaming-demo-result-20261006.md). See [the streaming deployment review](archive/reviews/streaming-demo-review-20261006.md)
for exact image/data hashes, costs, permission exceptions and approval scope.

- Replay all 884 transactions from 32 label-independently selected senders,
  using verified curated CSV and existing prior-day FX. Original timestamps
  remain unchanged. The producer caps its send rate at ten events/second, but
  sequential acknowledgements made the actual run take 1,049.545 seconds.
  This replay duration does not measure live ingestion latency.
- Kinesis partition keys hash the composite `(bank, account)`. One provisioned
  shard and Lambda concurrency/parallelization one preserve sender order.
  Per-sender ordinals detect missing events. This replaces the earlier
  account-only key and ambiguous "one shard, on-demand" sizing proposal.
- DynamoDB retains bounded exact preceding history and versioned state;
  conditional two-Put transactions commit state and unique results together.
  Atomic ADD alone cannot expire exact windows. There is no TTL tied to the
  historical dataset calendar; limits fail closed rather than truncating.
- Same-timestamp peers are excluded, duplicates do not advance history, and
  redelivery repairs S3 audit after a committed result. Failures stop at the
  first sequence, with bounded retry and AWS S3 failure archives.
- The same frozen model is used; scores are ranking outputs, not calibrated
  probabilities. No extra new-account feature or rule-score-60 threshold is
  inserted. SNS reports processing failures only. Transaction alerts and live
  dashboard publication remain outside this first demo's scope.
- All selected rolling features agree exactly and scores pass frozen tolerance
  in the final Linux image. The approved AWS replay passed all 884 result checks
  and 32 sender versions; consumption is Disabled. An idle shard costs about USD 13.69/month;
  disabling consumption does not stop that cost.

### 4.6 Orchestration (Step Functions) — concrete state machine

The first implementation in `infra/orchestration/` is deployed and verified:
`ValidateRequest → VerifyJob/Table/Workgroup → RejectExistingOutput →
Parallel[RunFrozenScoring (BQ1), RunTreasuryCorridors (BQ2)] →
VerifySuccessMarker → BusinessOutputsReady`.
It reuses verified curated data and the frozen model. Only read/notification
operations retry; compute tasks do not retry or overwrite partial output.
Catchable failures lead to SNS and FAILED. Every score and treasury aggregate
must still reconcile locally before a separately reviewed publication.
Training, ingestion, curation and dashboard export remain outside this version.
No daily replay is needed for a static dataset. An optional one-off EventBridge
Scheduler is implemented but absent from the reviewed plan and disabled by
default; activation needs a separately approved fresh run and UTC time.
See [the concrete orchestration review](archive/reviews/orchestration-review-20261006.md) for
the three additions, two existing updates, successful full execution, all-row
score/corridor reconciliation, tests, execution limits and costs.

### 4.7 Visualization — concrete mechanism

The local implementation uses checked-in HTML/CSS/JavaScript in `dashboard/`
and a generated `dashboard.json` snapshot. `scripts/build-dashboard.py` exports
data and copies those assets to a fresh output directory. Native SVG and HTML
charts need no CDN. Future AWS jobs should generate this data contract, with
static assets hosted separately on S3/CloudFront. The verified four-file snapshot
is now published at `https://d1n61tlw9pcrog.cloudfront.net/`; its origin is private.

- **Dashboard 1 (BQ1):** ranked transaction queue (not an account ranking),
  evidence drilldown, alerts by day, score distribution, period metrics and
  validation-only baseline comparisons. The original local export represents
  the sender cohort; `scripts/export-verified-batch.py` now exports all 5,078,345
  verified cloud transactions, with full-population scope shown explicitly.
  `scripts/build-model-dashboard.py` reloads the frozen model and adds a separately
  labelled experimental queue, same-period model/rule comparison and margin
  evidence. The original rule outputs and BQ2 data are retained; rule charts do
  not pretend to be model alerts. Training/explanations/export remain local;
  full frozen-model scoring ran in AWS, and the exported snapshot is AWS-hosted.
- **Dashboard 2 (BQ2):** directed bank and currency corridor rankings, daily
  USD volume and HHI trends, all-period/day selection, and CSV export. Shares
  and HHI include all pairs, even when tables show only the top 15.

Sankey, corridor-by-day heatmap, and payment-format mix are deferred enhancements.
The current corridor aggregate does not retain payment format; adding the mix
requires an additional mart. The initial views answer both business questions
without these extra charts. Neither local view is a live alerting system.

### 4.8 Infra/ops gaps

The live monthly budget is now USD 10 at the user's request, with the same alert
thresholds and no hard spending cap. `infra/scoring/` is prepared separately and
passes seven mocked tests. The separately approved scoring stack is now deployed;
its first 1,024-row run failed before scoring on the artifact guard. Exact Glue
job/account trust worked for that execution. See `archive/reviews/scoring-infrastructure-review.md`
for scoped writes and evidence. The approved localization retry scored all 1,024
rows exactly like the native fixture, then FAILED at S3 output commit; those
partial files are not valid pipeline output. A narrow EMRFS marker-permission fix
was applied, but the third run still FAILED at the folder check, with exact
1,024-row score parity. A direct offline test of Glue's Hadoop library confirms
the marker-disabled commit branch calls `mkdirs`. A fresh two-update, no-upload
plan explicitly enabled `_SUCCESS`; the approved fourth run **SUCCEEDED** after
120 execution seconds with the real S3 marker and exact native parity on all
1,024 rows. IDs, features, partitions and cohort top-100 order also reconcile.
This verifies the smoke path only; full-data scoring and publishing require
separate approval. See `archive/reviews/scoring-success-marker-review.md`.

2026-10-06 full-data preparation: the private scoring plan changes only the
input/output/temp scopes and expected count to 5,078,345, reusing frozen
artifacts, workers, ten-minute timeout, zero retries and explicit success markers.
`scripts/verify-cloud-scoring-full.py` checks all feature fingerprints and frozen
scores before producing a checked cache. The model dashboard can consume that
cache without replacing cloud scores with local predictions; rules and BQ2
remain unchanged. 29 targeted tests pass. The user subsequently approved two
updates and one full run: it succeeded in 205 execution seconds, and all
5,078,345 rows passed verification within the frozen-score tolerance. The real
encrypted success marker exists. The cloud-backed dashboard and four-file
release were built and browser-checked locally, then separately approved for
public hosting. Publication acceptance is recorded below.
See `archive/reviews/full-cloud-scoring-review.md` and `evidence/full-cloud-scoring-result-20261006.json`.

2026-10-05 hosting preparation: `infra/hosting/` is a separate, locally validated
stack with zero resources by default and explicit public-demo opt-in. Four mocked
tests pass. The offline release packager allowlists only runtime dashboard assets;
no deployment, uploads or publishing occurred during that initial preparation.
The separately approved 2026-10-06 hosting step added nine resources, uploaded
four hash-verified files and enabled one distribution. Public HTTPS/security,
origin restrictions and live browser acceptance passed with no Terraform drift.
See `archive/reviews/dashboard-hosting-review.md` and `evidence/dashboard-publication-result-20261006.json`.
Cloud scoring now has a locally implemented entry point and frozen artifact
packaging; `archive/reviews/cloud-scoring-preparation.md` records Linux runtime parity and paid-run
approval gates. The scorer is now deployed and its full run is verified. This does not change the verified
batch infrastructure or optional X-factor priorities.

Current foundation: `infra/terraform/`, `jobs/curate_transactions.py`, and
`planning/runbooks/aws-batch-runbook.md` define a manual 1,000-row curation smoke path.
Terraform validates locally and passes three mocked planning tests. All 30
Python tests, including Spark parity, pass in the official Glue 5 container;
the 24-resource foundation was deployed with user approval on 2026-10-05 and
verified with a no-change plan. The approved 1,000-row Glue/Athena smoke path now
passes cloud-to-local curation parity; a bounded full-data run was subsequently approved.
See `planning/aws-deployment-status.md`. This first version uses an explicit catalog schema plus Athena
partition projection instead of crawlers. The failure EventBridge rule only
notifies; the first bounded scoring/treasury orchestration is now verified.
Scheduled runs remain optional and absent. AWS
rolling features and stable source IDs are now implemented and Spark parity-tested;
full-population rule scoring and dashboard export now run locally from the
reconciled optimized cloud output. Full AWS scoring and dashboard publication
are now verified; training remains local. The approved scoring/treasury workflow
also passed full reconciliation. The bounded streaming replay passed all 884 result and 32 sender-history checks; its consumer is disabled. The rule baseline's full-population test top 100 contains zero
positives; the locally validation-selected model finds 44. Keep both experiments
exploratory and do not tune further on this already-used holdout. See
`planning/archive/reviews/cloud-insights-validation.md`, deployment status and the runbook for
actual full-run results and remaining gates.

- **IaC:** Terraform (HCL) — declarative, widely used for AWS, and decouples
  infra provisioning from the Glue PySpark/Lambda application code's language.
- **IAM:** least-privilege, one role per component (`GlueETLRole`,
  `LambdaScoringRole`, `StepFunctionsRole`) — no wildcard resource policies.
- **Encryption:** default SSE-S3 on all buckets, DynamoDB encryption-at-rest
  (on by default). No VPC — stated as a deliberate decision (everything here is
  serverless, talking to S3/DynamoDB over the AWS network), not an omission.
- **Data quality gate:** a Glue step validates no nulls in
  `Timestamp`/`From Bank`/`To Bank`/amount fields, amounts ≥ 0, and timestamps
  within the expected Sept 2022 range before feature engineering. Curation ran
  and reconciled separately. The scoring/treasury workflow checks contracts and
  fresh outputs; catchable scoring/query failures lead to SNS and FAILED.
- **Testing:** `pytest` unit tests for the feature functions in §4.3 against
  small fixture DataFrames, official Glue-container parity and manual AWS smoke
  runs before full-data curation/scoring. The orchestration adds request/contract,
  fresh-prefix, marker, failure-path and treasury tests plus mocked infrastructure
  tests. Its approved full execution passed every scored row and daily corridor;
  no separate small state-machine execution is claimed.
- **Cost estimate (HI-Small, worked):** `Trans.csv` 475MB → curated Parquet
  roughly 150MB after columnar compression. Athena at ~$5/TB scanned → a full-table
  query costs ~$0.0008, negligible even run hundreds of times. Glue ETL on
  2–5 G.1X workers for a 475MB job finishes in a few minutes → low single-digit
  DPU-hours → a few dollars. SageMaker training on a small instance for minutes →
  under $5. Kinesis (1 on-demand shard) + Lambda invocations for a demo window →
  cents. **Estimated total demo cost: well under $50**, against the $250 cap —
  included here so the budget claim in §2 is checkable rather than asserted.

## 5. EDA write-up — done, one gap remains

`scripts/eda.ipynb` has been run against `HI-Small` and its "4. Summary" section
is filled in; §4.3's rule-based weights above are now calibrated against those
real numbers rather than placeholders. One gap is still open: the notebook only
profiles **transaction-level** fields (payment format, self-txn, currency
mismatch, raw amounts) — it does not compute the **behavior-level** velocity
features (`txn_count_1h`, `distinct_counterparties_24h`, etc.), since those
require per-account rolling aggregation that's a Glue ETL/Spark job, not a
pandas notebook scan. The `+30` fan-out weight in §4.3 is therefore still a
hypothesis (grounded in the `Patterns.txt` attempt headers) rather than an
EDA-measured number, and should be re-validated once the ETL actually computes
those columns. Re-running the notebook against `LI-Small` to compare illicit
ratios across variants (mentioned in §4's summary) is also still open but not
blocking.
