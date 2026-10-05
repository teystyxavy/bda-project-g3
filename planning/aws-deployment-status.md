# AWS foundation deployment — 2026-10-05

Latest budget update: the user requested USD 10 monthly. AWS UpdateBudget and
subsequent DescribeBudget verified USD 10, with the existing actual-spend alerts
retained. Terraform defaults/examples/tests and private variables match; this is
an alert, not a hard spend cap. No scoring resources or paid jobs were started.
USD 5 figures below describe earlier plans and billing snapshots, not the current
budget. See `scoring-infrastructure-review.md` for the prepared scoring stack.

The user approved infrastructure deployment. Terraform applied the reviewed
USD 5 monthly-budget plan: **24 resources added, zero changed, zero destroyed**.
A subsequent authenticated Terraform plan returned **no changes**.

| Component | Deployed name |
|---|---|
| Region | ap-southeast-1 (Singapore) |
| S3 bucket | is459-g3-588738581042-ap-southeast-1 |
| Glue job | is459-g3-curate |
| Catalog database/table | is459_g3_curated.smoke_transactions |
| Athena workgroup | is459-g3-analysis |
| Monthly account budget | is459-g3-account-monthly |
| Failure topic | is459-g3-failures |

Initial post-deployment checks verified all four S3 public-access blocks enabled and AES256
default encryption. At that point the bucket contained only `scripts/curate_transactions.py`
(7,552 bytes) and Glue reported zero job runs. The account budget reports USD 5 per
month; its configured notices are 50%, 80% and 100%, not an automatic spending cap.

The SNS subscription was subsequently confirmed (`PendingConfirmation=false`).
The failure route has not been exercised with a job failure.

## Approved cloud smoke verification

The user subsequently approved the 1,000-row smoke run. All three small inputs
were uploaded. Glue run
`jr_31780dedd67135e0dd79d3ae9c270963127a1872ba6db3986938c9e6f06d574d`
succeeded, completing at 2026-10-05 00:21:28 Singapore time with 130 reported
execution seconds on two G.1X workers.

Downloaded Parquet matches the local reference across all 1,000 rows and 22
fields (floating comparisons use rtol 1e-10 and atol 1e-6). Both USD totals are
1,644,403,430.34; missing account/FX counts and positive labels are zero.
The 176 bank-corridor rows reconcile to the same transaction count and volume.

Athena query IDs, both SUCCEEDED:

- Reconciliation: `9fd42fb9-ddc4-4adf-949f-0bab3692bd5f` (23,814 bytes scanned).
- Corridors: `e3879d2f-537b-4f16-b145-bd3c77f43c3d` (8,942 bytes scanned).

Evidence is under `output/aws-smoke-cloud/`: downloaded curated Parquet,
`parity.json`, `reconciliation.csv`, and `corridors.csv`. Re-run field comparison
using `python scripts/verify-aws-smoke.py --curated output/aws-smoke-cloud/curated`.

Estimated Glue compute: 130 / 3600 × 2 × USD 0.44 = USD 0.03178, using the
[Singapore AWS Glue price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSGlue/current/ap-southeast-1/index.json).
This is an estimate, not a billed total; Athena minimum query charges, S3,
logs, tax and any other account activity are separate.

Next: implement stable transaction IDs and exact rolling behaviour features in
Spark, verify parity with the local baseline, then prepare full-data execution
and dashboard export. Do not use this first-rows sample to evaluate model quality
or history-dependent behaviour.

## Full-run preparation — 2026-10-05 08:00 Singapore

The user requested the next run. Local job changes add physical source-row IDs
(`HI-Small:<zero-based-row>`) from a single immutable, one-record-per-line CSV,
and opt-in `--behaviour true` exact Spark history windows. They preserve duplicate
transactions and exclude all simultaneous peers. New Spark tests compare boundary,
counterparty and bank-key behaviour with the local implementation. These changes
were initially not deployed or Spark-verified; see the successful retry below.

18 native Python tests pass (Spark module skipped); Docker startup failed with
an inaccessible `sailor-ingest.sock` socket before the Glue container could run.
Do not deploy or launch full-data execution until the Spark parity tests pass.
No new cloud uploads, Terraform changes or paid job runs were performed this turn.

### Retry — 2026-10-05 19:31 Singapore

Docker recovered. The official Glue 5 container completed all **23 tests**
successfully, including source-row ID stability, duplicate preservation and
exact rolling-feature parity at window boundaries and simultaneous timestamps.
The 13 warnings are upstream deprecation warnings. The extended job remains
undeployed: AWS reports an expired browser-login session, so account, budget
and job-limit checks could not be refreshed. A renewed `is459-g3` login is
required before preparing and applying the updated Terraform plan or starting
the requested full-data run. No paid run or AWS mutation occurred on this retry.

The budget API reported USD 0.571 actual and USD 5.397 forecast monthly spend
(last update 2026-10-05 00:39 Singapore). Billing lags; the forecast exceeds the
USD 5 target. Keep the existing two-worker, ten-minute timeout and no retries.
At the previously verified USD 0.44/DPU-hour rate, ten compute minutes would be
USD 0.14667, excluding storage, queries, logs and other account activity.

Terraform state remains outside the repository at
`%LOCALAPPDATA%\IS459-G3\terraform.tfstate`. Preserve it for changes and cleanup.
The reviewed plan has been applied; create a fresh plan for future changes.
No credentials, Terraform state or private variables are committed.

## Authorized full-data attempt — TIMEOUT

After renewed login as `g1team3` in account `588738581042`, the budget reported
USD 0.743 actual and USD 5.87 forecast (updated 2026-10-05 18:50 Singapore).
The forecast is above the USD 5 target; the budget remains an alert, not a cap.

Applied two reviewed Terraform plans: one script update, then one new catalog
table, `is459_g3_curated.full_transactions`. No services were scheduled, no
worker/timeout/retry limits increased, and nothing was destroyed. All 24 Python
tests passed in the official Glue 5 container, along with three mocked Terraform
tests and Terraform validation.

Uploaded the three full inputs to `raw/full-20261005/` and started exactly one
run with 5,078,345 expected rows and `--behaviour true`:
`jr_9e9aaf9175cabada3c026a8c72175613b929ea20fd3bc3f21dc3f57a3e7172cf`.
It ended **TIMEOUT** at 2026-10-05 19:45:56 Singapore after **600 execution
seconds** on two G.1X workers. No retry was started.

**Do not use `full_transactions` or `curated/full-20261005/transactions/` for
business results.** There are 101 partial objects totaling 258,296,984 bytes.
They were retained for diagnosis, not deleted. No full-data Athena query or
dashboard refresh was performed. Smoke results remain intact.

Stage 128 logged six completed tasks out of eight; partitions 5 and 6 did not
log completion before the timeout. Output-writing tasks were active. Uneven
sender-window workload is a likely bottleneck, not a proven root cause yet.
Next: profile and optimize the history calculation and repeated validation
passes locally before proposing another paid run. A retry requires a fresh
output prefix and reviewed catalog-location change; never append to this one.

Estimated Glue compute is 600 / 3600 × 2 × USD 0.44 = **USD 0.14667**,
excluding S3 requests/storage, logs, taxes and other account activity. This is
not an invoice or a guarantee of total account spending.

Input SHA256 hashes, in transaction/accounts/FX order:

- `B19D39F515523373F991B689C07E11E7B0B95C17A2C27A87D91584AE16C5B040`
- `786808526E33CFC441212DD6FCCDA7EDFC24172149BED59C6EF59B186836B014`
- `D6967EF095D43E165BC730636E76C1482DA89E8828B0426C902FF28C7EE3176F`

The local request is recorded in `output/full-20261005/run-request.json`.
`scripts/verify-aws-full.py` is ready for a future successful run; it has not
validated this incomplete output.

Subsequent local-only optimization and benchmark evidence is recorded in
[Glue performance validation](glue-performance-validation.md). The queue-based
version passes 30 container tests and exact full-history/cohort comparison, but
is not deployed. The initial local launch was blocked by approval-service
capacity; the user-authorized retry then succeeded in 162.992 processing
seconds. Complete local write/read-back and separate full ID/corridor plus
cohort-feature reconciliation passed. Evidence is in
`output/full-local-optimized-20261005/`. No additional cloud run occurred.

## Optimized cloud retry — SUCCEEDED and reconciled

The user subsequently approved one bounded retry. Before it started, AWS still
reported USD 0.743 actual and USD 5.87 forecast (billing update 18:50 Singapore,
so not inclusive of all later activity). Applied a reviewed Terraform plan with
zero additions, two updates (script and full-table location), zero deletions.
Post-apply plan: no changes. The stack now contains 25 resources.

Reused `raw/full-20261005/` inputs. Run
`jr_6f882796b7df87e127d63cdec9de44039b9dcc3af902eaddf83e8279fdd64486`
completed **SUCCEEDED** at 2026-10-05 21:34:18 Singapore, with 386 execution
seconds, two G.1X workers, ten-minute timeout and no automatic retries.
Output: `curated/full-optimized-20261005/transactions/`. The Terraform-managed
`full_transactions` table now points only to this successful output. The old
`curated/full-20261005/transactions/` prefix remains partial and invalid.

Application timings: input 82.293 seconds; curation 84.963; features/write/
read-back 173.948; total application processing 341.204. Glue's 386 reported
seconds include additional runtime overhead.

Athena query `27e82bce-0ed2-4616-80e4-243855b0a12b` ran
`sql/reconcile-full.sql` successfully, scanning 179,353,781 bytes. Results:

- 5,078,345 rows and distinct transaction IDs; source index range 0–5,078,344.
- 5,177 positive labels; zero missing metadata, FX or behaviour values.
- USD totals: 1,747,869,572,934.3848 same-day and
  1,748,564,754,800.0552 prior-day (distributed floating summation).

Downloaded Parquet passed `scripts/verify-aws-full.py`: every source ID is
unique and present; key fields and all five history features match the saved
256,557-row cohort; all 1,099,714 daily bank/currency corridor aggregates match
the full local reference. Floating comparisons use rtol 1e-10 and atol 1e-6.
The reference same-day USD total is 1,747,869,572,934.3916.

Evidence under `output/full-cloud-optimized-20261005/`: `run-request.json`,
downloaded `curated/`, `reconciliation.csv`, and `parity.json`.

Estimated Glue compute: 386 / 3600 × 2 × USD 0.44 = USD 0.09436. This excludes
Athena, S3 storage/requests, logs, taxes and other account activity; it is not
an invoice. No additional run, schedule or automatic retry was started.

Next: consume the verified cloud data for BQ1 scoring and refreshed dashboard
exports. ML training, dashboard hosting, orchestration and streaming remain
separate pending work. Partial-output cleanup still requires explicit approval.

## Full-population local insight export

Consumed the verified optimized-cloud Parquet locally and exported all 5,078,345
rule scores, period-specific review queues, treasury aggregates and both static
dashboard views to `output/cloud-insights-20261005/`. Counts, unique source IDs,
5,177 labels and treasury USD totals reconcile. Native tests: 21 passed with the
Spark module skipped. Browser checks confirmed period/day selection, queue
drilldown and full-population scope with no captured console errors.

The rule baseline achieves 2/100 positives in validation but 0/100 in the main
test queue. No test-set retuning was performed. See
[insight validation](cloud-insights-validation.md) for metrics and limitations.
This step ran locally and started no additional AWS jobs. ML comparison, cloud
scoring, dashboard hosting and X-factor layers remain pending.

## Local ML comparison — no AWS mutation

Two bounded XGBoost candidates trained locally on September 1–6 only. Validation
selected depth 5; the choice was saved before loading the test period. It finds
44/100 positives on the main test queue versus 0/100 for rules; average precision
is 0.135546 versus 0.006758. Models, schema, hashes and label-free explained queues
are under `output/model-comparison-20261005/`. 24 local ML-environment tests pass
with the native Spark module skipped. See [model validation](local-model-validation.md).
No additional AWS job, deployment, upload or automatic promotion occurred.

## Local model dashboard integration

The saved model reloads with artifact/source hash checks and reproduces all three
period results. A separate local build at `output/model-dashboard-20261005/`
adds model/rule queue switching, side-by-side metrics and readable model-margin
evidence while preserving treasury and rule data. Native tests: 24 passed with
one Spark module skipped. See [dashboard checks](model-dashboard-validation.md)
for rendered checks and the unverified browser download-event check.
No retraining, AWS calls, paid jobs, hosting deployment or production promotion.
