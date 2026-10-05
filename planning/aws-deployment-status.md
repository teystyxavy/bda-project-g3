# AWS foundation deployment — 2026-10-05

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
