# Proposal: faster AWS ingestion and a realistic banking data flow

Status: phases A/B full-rebuild optimisations are now implemented and deployed;
see [implementation](../runbooks/pipeline-optimisation-implementation.md) and
[acceptance/observed timings](../evidence/pipeline-optimisation-result-20261006.json).
Incremental ingestion and aligned streaming in phases C/D remain proposed.
The [Kinesis dashboard improvement](kinesis-dashboard-improvements.md) proposes
a smaller first D milestone: a separate Incoming AML replay queue served through
S3/CloudFront. Its one-minute publisher targets 90-second visibility, distinct
from the broader sub-30-second scoring target below.
This proposal's targets and older benchmark are retained as planning context.

## Objective and measured baseline

Make normal operation depend on new or corrected transactions rather than rebuilding all 5,078,345 historical rows. Retain a reproducible full rebuild for initial loading, independent audit, code/model changes and recovery. Keep AML investigation and treasury concentration as separate business outputs.

The original full run took 24m59s. Approximately 1 minute was acquisition, 7.2
minutes curation, 7.6 minutes baseline verification, 4 minutes scoring/corridor
analysis and 5.1 minutes export/publication. Baseline compute ended 2m26s before
orchestration continued, but later service timestamps show its final status was
modified only 1.645 seconds before the next state. The earlier attribution of that
whole gap to polling was incorrect: it mainly included output/service finalisation.
The optimised rebuild measured 20m55.083s, about 16.3% faster, with identical inputs
and business ranking outputs. Athena's original query took only 7.6 seconds, so
query compute remains a low priority for this run's latency.

Current costs and timing are driven by repeated full scans, serial dependencies, independently started compute jobs, and completion detection. The routine pipeline already reuses the frozen model; retraining was absent from this 25-minute run.

## Proposed operating model

```text
Initial historical load / exceptional rebuild
Kaggle + historical FX → existing verified full batch → committed baseline

New or corrected transactions
Source manifest → immutable S3 raw → validation / deduplication
                                      ↓
                      affected transactions + required history
                                      ↓
                         Glue features and frozen scoring
                                      ↓
                    scored transactions + daily aggregates
                                      ↓
                     reconciliation → dashboard release

Optional operational AML path
Transaction events → existing Kinesis / Lambda / DynamoDB
                    → provisional queue + S3 audit
                    → event-time reconciliation with batch

Separate model lifecycle
Mature labels + drift review → SageMaker candidate training
                            → chronological evaluation → explicit promotion
```

Kaggle is a static historical dataset, not a live banking feed. For the course demonstration, acquire it once, preserve original source-row identities, split it into chronological batches, and replay arrival scenarios. A real deployment would connect approved ledger exports, transaction APIs or change events. New batches must not be presented as newly occurring real transactions.

## Changes in priority order

| Priority | Change | Why it reduces time | Controls retained |
|---|---|---|---|
| 1 | After curation, run baseline verification, frozen scoring and Athena concurrently | Removes approximately four minutes of serial dependency in the observed run | Cheap schema/manifest checks run first; publication waits for every branch. Training waits for its verified baseline. Failed verification may waste overlapping score compute. |
| 1 | Inspect job completion with a bounded 15–30-second status loop | Makes completion checks explicit and bounded; measured gain came mainly from parallelism, not a two-minute polling delay | Check actual service success, output commit and proof; retain timeout, cancellation/recovery behavior and no automatic paid-job resubmission. Audit existing event/rule permissions first. |
| 2 | Cache individual stage outputs | Reuse curation after a dashboard-only change, or reuse features when only the model changes | Keys include relevant input/FX/account hashes, code/runtime version, feature schema and model digest. Reuse only successful immutable outputs with verified receipts; expire or invalidate intentionally. |
| 2 | Stop rebuilding the baseline dashboard before building the final dashboard | Avoid intermediate rendering, repeated scans and duplicate full model inference | Persist baseline rule scores, frozen-model scores, summaries and model metadata; render the final views from these outputs. Keep independent audit checks separate and explicit. |
| 2 | Read only the inputs each worker needs | Export currently stages raw, curated, baseline, scored and model inputs; reducing its input list can avoid redundant downloads | Document and test each stage's contract before removing any input. Full audit keeps sources needed for independent comparisons. |
| 2 | Profile and tune Glue | Reduce expensive scans, sorting and shuffle work, then measure whether more workers help | Keep chronological windows and composite bank/account keys intact; compare output parity. Retain `txn_date` partitions, prune dates and select required columns. Compact only when measurements show small-file overhead. |
| 3 | Add incremental transaction processing and serving aggregates | Normal refreshes process a delta and its necessary history instead of the whole archive | Stable IDs, deduplication, event-time checkpoints, affected-range recomputation and complete reconciliation. |
| 4 | Align the optional stream with the batch model and serving data | AML users can see new events without waiting for a full batch dashboard rebuild | Shared feature/FX/model contracts; provisional/final status; bounded state; duplicate-safe effects; batch reconciliation. Existing consumer stays disabled until an approved demonstration. |

AWS documents completion delays for synchronous integrations and partition pushdown to reduce data scans: [Step Functions troubleshooting](https://docs.aws.amazon.com/step-functions/latest/dg/troubleshooting.html), [Glue pushdown](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-pushdown.html). SageMaker Pipelines also supports successful-step caching, but our orchestration is Step Functions; implement compatible cache receipts in the existing control plane rather than claiming that feature is already enabled or adding a second orchestrator solely for caching. [SageMaker caching](https://docs.aws.amazon.com/sagemaker/latest/dg/pipelines-caching.html)

## Incremental correctness

1. **Source identity and checkpoints.** In a bank, retain the source transaction ID and revision. For this dataset, identify a row with its immutable file hash and original row offset; carry that identity into every chronological batch. Splitting or replaying a file must not renumber its rows. Record both event time and arrival time. Advance a committed checkpoint only after validated output is published.
2. **Duplicate-safe processing.** Persist batch identities and transaction/revision keys. Replayed input must not increase counts, treasury volume or duplicate alerts. Do not infer that a retryable Lambda or S3 event is delivered exactly once. Use conditional writes/transactions and versioned output commits.
3. **History across batches.** A midnight boundary must not reset sender velocity or fan-out. Read the required lookback for affected senders or preserve an equivalent bounded state. Exclude the current timestamp and simultaneous peers as the existing feature contract does. Include prior-day FX and account metadata valid at the transaction's event time.
4. **Late arrivals and corrections.** Establish an explicit allowed-lateness policy and watermark. Recompute affected sender features through the maximum configured lookback horizon; repair impacted scores and daily aggregates. Beyond-window corrections use a controlled backfill. An FX correction invalidates affected currency/date valuations; a model change triggers rescoring under a new model version.
5. **Reconciliation.** Account for accepted, quarantined and duplicate input. Enforce unique scored rows, complete score coverage, FX validity and transaction/USD totals. Keep bank-pair and currency-pair aggregates as separate grains; do not sum their duplicated populations together.

Simple date-filtered appending or Glue bookmarks alone cannot guarantee these properties: corrected transactions and rolling histories require semantic reconciliation as well as file tracking.

For the course scope, start with versioned date-partitioned Parquet plus an affected-partition manifest. Use a conditionally committed dataset manifest to give readers a consistent version. Consider Iceberg only if frequent corrections justify transactional upserts and their maintenance work; Athena `MERGE INTO` is supported for Iceberg with engine version 3. [Athena documentation](https://docs.aws.amazon.com/athena/latest/ug/querying-iceberg-merge-into.html)

## Dashboard and ML changes

Create serving outputs alongside the pipeline: daily corridor count/USD volume, rule alert summaries, score distributions and ranked review candidates. Recompute daily HHI and top-five share from all relevant bank-pair aggregates, not just the displayed top corridors. Generate the static dashboard from these smaller outputs; preserve immutable releases, the current pointer and rollback evidence.

A global top-K over disjoint append-only batches can be obtained from the union of each batch's top-K. Corrections/deletions require updating or recomputing affected candidates; do not blindly retain a previously selected queue. If investigators can change K or filters, retain enough scored data to answer those queries accurately.

Calculate explanations for displayed queue candidates, not every transaction. Use persisted scores for ranking rather than predicting again solely to construct the dashboard. Exact offline PR-AUC still needs complete evaluation-period score/label data; retain it in the model evaluation/audit workflow. Live banking labels arrive later, so do not claim current operational precision/recall from unlabeled incoming events.

Keep SageMaker training separate from ingestion. Trigger it explicitly or after a reviewed drift/data-quality/label-maturity condition. Freeze candidate selection before test evaluation, compare against the rule baseline, retain model/feature/FX versions, and require explicit promotion. Do not fit thresholds or categorical vocabularies on incoming validation/test or future data.

## Fast routine mode and full audit mode

The fast routine mode still checks every delta row's identity, schema, uniqueness, FX coverage, score completeness and aggregate reconciliation. Run independent model parity on a documented deterministic sample, plus fixed golden fixtures; retain complete native parity and independent historical-feature reconstruction in the full audit mode. Describe the receipt accurately: sampled parity is not exhaustive parity.

Full audits run for the initial load, model/feature/runtime changes, material backfills, parity failures and final submission evidence. Schedule periodic audits only with explicit recurring-run authorization. A routine mode is a changed assurance policy and must be reviewed before deployment; it is not silently substituted for the current verified full path.

## Delivery phases, timing targets and costs

| Phase | Deliverable | Target to benchmark | Spending approach |
|---|---|---|---|
| A | Parallel branches and prompt completion detection; current full checks preserved | Full rebuild around 18–20 minutes | Existing services; additional status calls/transitions; avoid increasing compute first |
| B | Stage cache, persisted score reuse, smaller serving outputs and streamlined worker inputs | Full rebuild target 12–16 minutes, subject to profiling | Reduce scans and job overhead; keep full audit available; benchmark before increasing workers |
| C | Chronological delta ingestion, state/checkpoint and repair logic | Representative daily batch around 3–8 minutes | Keep worker startup floors visible; define batch row count, history volume and concurrency for each measurement |
| D | Optional aligned AML stream and batch reconciliation | Provisional scores within 30 seconds under defined demo load | Time-bounded demonstration; account for provisioned Kinesis shard and DynamoDB requests; final scores follow watermark/reconciliation |

All figures above are proposed targets, not achieved SLAs or additive savings. They depend on data size, account-history distribution, source delays and AWS startup/completion behavior. Smaller Spark batches can be dominated by startup time; choose micro-batch frequency after benchmarking. The existing unchanged-input cache should continue bypassing paid batch work.

Preserve the USD250 course limit and USD10 monthly alert. Record scan bytes, worker/job duration, retries, billed duration where available and billing snapshots with their lag. Use a concurrency cap and reviewed run budget before starting a job; alerts are not a hard spend cap. No provisioned endpoint, always-on EC2, NAT gateway, Redshift, RDS, QuickSight or Marketplace purchase is proposed. No new daily schedule or streaming activation occurs under this proposal.

## Acceptance and demonstration

- Compare one full rebuild with sequential chronological batches: identical accepted transaction set, feature values, scores, top queues and corridor totals within existing numerical tolerances.
- Replay one batch twice: unchanged committed outputs and no duplicate side effects.
- Test a cross-midnight history, simultaneous peers, a late arrival, a correction and an FX revision. Repair only the affected scope while matching the full reference result.
- Inject source/API failure and a failed worker: prior dashboard preserved, checkpoint unchanged, outputs recoverable and retry controlled.
- Benchmark at least three authorized comparable runs per candidate configuration when cost permits; report median/range and cost, not a single best run. For stronger percentile claims, collect a larger representative sample.
- Show ingestion lag, event-time coverage/watermark, last successful release, rejected/duplicate counts, model version and cost estimates. Keep metrics suitable for the AML and treasury audiences rather than showing raw infrastructure details in the dashboard.

Recommended next scope after the implemented A/B work: C with a chronological
daily-batch demonstration, then D if the team can explain and verify event-time/state
behavior. Measured full-rebuild results are recorded separately; the delta-mode
acceptance examples above do not describe functionality already deployed.
