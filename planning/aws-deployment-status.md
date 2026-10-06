# AWS foundation deployment â€” 2026-10-05

**Dashboard update, 7 October 2026:** BQ1/BQ2 exploration and the public read-only
Ingestion status feed are deployed. The status stack added 10 resources with no
updates/deletions. The separately reviewed bundled worker update retained its
predecessor artifacts. Both post-apply plans show no drift. Public asset hashes,
GET status and unsupported-route checks passed; the live-response render harness
shows the latest run and six recent runs. Final browser visual acceptance remains
unverified because the browser tool failed to initialize. No paid batch jobs were
started. See [evidence](evidence/dashboard-status-exploration-result-20261007.json)
and [status setup](../infra/dashboard-status/README.md).

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
The existing `scripts/ingestion/ingest.ps1` entry point runs AWS acquisition through publication;
`-Train` adds candidate training and `-Rerun` bypasses caches.

**AWS acquisition revision, 6 October 2026:** the dedicated Lambda and wrapper
workflow are deployed: nine additions, no existing updates/deletions. The read-only
post-deployment and post-run plans show no changes. Smoke and full acceptance passed,
including live source acquisition, native score parity, corridor reconciliation and public hashes;
see [evidence](evidence/cloud-ingestion-deployment-result-20261006.json). The default launcher
selects this wrapper; `-LocalAcquisition` retains the previously verified path.

**Automated ingestion update, 6 October 2026:** all 33 reviewed additions are
deployed (3 SageMaker foundation + 30 ingestion resources). The 1,000-row AWS smoke
completed Glue curation/scoring, SageMaker baseline/export verification, Athena
reconciliation and conditional dashboard publication. The full 5,078,345-row
run with candidate training also passed and published. See [deployment evidence](evidence/ingestion-deployment-result-20261006.json)
and [working runbook](runbooks/ingestion-runbook.md). Earlier diagrams document the previous
verified runs; the new workflow is defined in `infra/ingestion/workflow.asl.json`.

Latest budget update: the user requested USD 10 monthly. AWS UpdateBudget and
subsequent DescribeBudget verified USD 10, with the existing actual-spend alerts
retained. Terraform defaults/examples/tests and private variables match; this is
an alert, not a hard spend cap. That budget-only step started no scoring jobs.
USD 5 figures below describe earlier plans and billing snapshots, not the current
budget. Subsequent authorized scoring smoke work deployed nine separate
resources and staged nine checked files. One 1,024-row run failed before scoring
on the artifact guard after 79 execution seconds; strict Glue trust worked for
this execution. The approved localization retry then applied two updates, staged
eight verified artifacts and FAILED at output commit after 126 execution seconds.
Its four partial Parquet files contain 1,024 rows with exact native score parity;
there is no success marker, and they are not valid pipeline output. A narrow
EMRFS marker fix was approved and applied, but the third paid run also FAILED at
the folder check after 105 execution seconds. Its partial 1,024-row scores match
the native fixture exactly. The user then approved explicit success-marker
configuration and one fourth run. It **SUCCEEDED** on 2026-10-06 after 120
execution seconds, with a real `_SUCCESS` and ordinary 1,024-row reconciliation
showing exact native score parity. Two updates applied with zero uploads;
post-apply plan showed no changes. See `archive/reviews/scoring-success-marker-review.md`.
The separately approved full-data scoring run subsequently **SUCCEEDED** in 205
execution seconds (411 DPU-seconds). All 5,078,345 rows passed reconciliation;
the real encrypted success marker exists. The cloud-backed dashboard and local
release are complete. See [full scoring result](evidence/full-cloud-scoring-result-20261006.json).
The separately approved hosting stack subsequently added nine resources, uploaded
four verified files and enabled one distribution. Public HTTPS, origin denial
and both dashboard views passed acceptance; the post-apply plan shows no changes.
Live demo: [d1n61tlw9pcrog.cloudfront.net](https://d1n61tlw9pcrog.cloudfront.net/).
See [publication result](evidence/dashboard-publication-result-20261006.json).
The separately approved orchestration extension added three resources and updated
only the scorer's output/temp arguments and matching permissions. Both post-apply
plans show no drift. The single execution `full-orchestrated-20261006` SUCCEEDED
in 232.469 seconds, with 18 states entered and no retries. Its Glue run used 180
execution seconds / 361 DPU-seconds; all 5,078,345 rows passed score, feature,
identity and partition checks. The treasury query scanned 86,039,814 bytes;
all 1,082,185 directed daily corridor rows passed local count/volume/share/HHI
reconciliation. No scheduling or dashboard changes. See
[orchestration review](archive/reviews/orchestration-review-20261006.md) and
[result metadata](evidence/orchestration-result-20261006.json).
See `archive/reviews/scoring-infrastructure-review.md` for run evidence, retained private state
and retry review. Foundation resources and verified curation remain unchanged.

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
using `python scripts/validation/verify-aws-smoke.py --curated output/aws-smoke-cloud/curated`.

Estimated Glue compute: 130 / 3600 Ã— 2 Ã— USD 0.44 = USD 0.03178, using the
[Singapore AWS Glue price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSGlue/current/ap-southeast-1/index.json).
This is an estimate, not a billed total; Athena minimum query charges, S3,
logs, tax and any other account activity are separate.

Next: implement stable transaction IDs and exact rolling behaviour features in
Spark, verify parity with the local baseline, then prepare full-data execution
and dashboard export. Do not use this first-rows sample to evaluate model quality
or history-dependent behaviour.

## Full-run preparation â€” 2026-10-05 08:00 Singapore

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

### Retry â€” 2026-10-05 19:31 Singapore

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

## Authorized full-data attempt â€” TIMEOUT

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

Estimated Glue compute is 600 / 3600 Ã— 2 Ã— USD 0.44 = **USD 0.14667**,
excluding S3 requests/storage, logs, taxes and other account activity. This is
not an invoice or a guarantee of total account spending.

Input SHA256 hashes, in transaction/accounts/FX order:

- `B19D39F515523373F991B689C07E11E7B0B95C17A2C27A87D91584AE16C5B040`
- `786808526E33CFC441212DD6FCCDA7EDFC24172149BED59C6EF59B186836B014`
- `D6967EF095D43E165BC730636E76C1482DA89E8828B0426C902FF28C7EE3176F`

The local request is recorded in `output/full-20261005/run-request.json`.
`scripts/validation/verify-aws-full.py` is ready for a future successful run; it has not
validated this incomplete output.

Subsequent local-only optimization and benchmark evidence is recorded in
[Glue performance validation](archive/reviews/glue-performance-validation.md). The queue-based
version passes 30 container tests and exact full-history/cohort comparison, but
is not deployed. The initial local launch was blocked by approval-service
capacity; the user-authorized retry then succeeded in 162.992 processing
seconds. Complete local write/read-back and separate full ID/corridor plus
cohort-feature reconciliation passed. Evidence is in
`output/full-local-optimized-20261005/`. No additional cloud run occurred.

## Optimized cloud retry â€” SUCCEEDED and reconciled

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

- 5,078,345 rows and distinct transaction IDs; source index range 0â€“5,078,344.
- 5,177 positive labels; zero missing metadata, FX or behaviour values.
- USD totals: 1,747,869,572,934.3848 same-day and
  1,748,564,754,800.0552 prior-day (distributed floating summation).

Downloaded Parquet passed `scripts/validation/verify-aws-full.py`: every source ID is
unique and present; key fields and all five history features match the saved
256,557-row cohort; all 1,099,714 daily bank/currency corridor aggregates match
the full local reference. Floating comparisons use rtol 1e-10 and atol 1e-6.
The reference same-day USD total is 1,747,869,572,934.3916.

Evidence under `output/full-cloud-optimized-20261005/`: `run-request.json`,
downloaded `curated/`, `reconciliation.csv`, and `parity.json`.

Estimated Glue compute: 386 / 3600 Ã— 2 Ã— USD 0.44 = USD 0.09436. This excludes
Athena, S3 storage/requests, logs, taxes and other account activity; it is not
an invoice. No additional run, schedule or automatic retry was started.

Subsequent work consumed the verified data for local ML training/analytics,
verified full AWS frozen-model scoring and public dashboard hosting. Orchestration
is now verified; streaming is prepared locally and awaits cloud approval. Partial-output cleanup still requires explicit approval.

## Full-population local insight export

Consumed the verified optimized-cloud Parquet locally and exported all 5,078,345
rule scores, period-specific review queues, treasury aggregates and both static
dashboard views to `output/cloud-insights-20261005/`. Counts, unique source IDs,
5,177 labels and treasury USD totals reconcile. Native tests: 21 passed with the
Spark module skipped. Browser checks confirmed period/day selection, queue
drilldown and full-population scope with no captured console errors.

The rule baseline achieves 2/100 positives in validation but 0/100 in the main
test queue. No test-set retuning was performed. See
[insight validation](archive/reviews/cloud-insights-validation.md) for metrics and limitations.
This step ran locally and started no additional AWS jobs. ML comparison, cloud
scoring, dashboard hosting and X-factor layers remain pending.

## Local ML comparison â€” no AWS mutation

Two bounded XGBoost candidates trained locally on September 1â€“6 only. Validation
selected depth 5; the choice was saved before loading the test period. It finds
44/100 positives on the main test queue versus 0/100 for rules; average precision
is 0.135546 versus 0.006758. Models, schema, hashes and label-free explained queues
are under `output/model-comparison-20261005/`. 24 local ML-environment tests pass
with the native Spark module skipped. See [model validation](archive/reviews/local-model-validation.md).
No additional AWS job, deployment, upload or automatic promotion occurred.

## Local model dashboard integration

The saved model reloads with artifact/source hash checks and reproduces all three
period results. A separate local build at `output/model-dashboard-20261005/`
adds model/rule queue switching, side-by-side metrics and readable model-margin
evidence while preserving treasury and rule data. Native tests: 24 passed with
one Spark module skipped. See [dashboard checks](archive/reviews/model-dashboard-validation.md)
for rendered checks and the unverified browser download-event check.
No retraining, AWS calls, paid jobs, hosting deployment or production promotion.

## Local streaming preparation â€” no deployment

The optional 884-event, 32-sender replay reproduces frozen batch features/scores
in the final Linux Lambda image. Transactional state, durable-result redelivery,
SDK serialization and acceptance guards are tested. Sixty-four targeted Python
tests pass with one environment-dependent skip; eleven core/SDK tests pass in
the final Lambda image. Five mocked streaming infrastructure tests pass.
See [the streaming review](archive/reviews/streaming-demo-review-20261006.md) for approval gates,
exact evidence, permission exceptions, costed resource scope and limitations.
This records local preparation, not verified Kinesis/Lambda/DynamoDB operation.

## Partial streaming deployment â€” no replay

The approved scope deployed ten streaming resources and uploaded the exact
reviewed Lambda image. Frozen model initialization passed in AWS. ECR needed
the repository-attached policy format; Lambda destination validation then
rejected prefix-only S3 write permission, including a narrow bucket-ARN repair.
No mapping exists, no transaction events were sent, and state/audit remain empty.

A safer isolated failure bucket is prepared, not deployed: the authenticated
plan shows four creates (three bucket/security resources and the already-approved
mapping), one execution-policy update, zero deletes. Six mocked tests pass and
the revised identity policy has no Access Analyzer findings. See the
[revised review](archive/reviews/streaming-failure-isolation-review-20261006.md) and
[partial result](evidence/streaming-deployment-result-20261006.json).
The image scan has 8 high, 6 medium and 1 low OS-package findings, documented
with the bounded-use assessment; do not claim a clean scan.
The shard continues charging USD 0.0184/hour. No cleanup was performed.

## Completed streaming acceptance â€” consumer disabled

The later approved isolated failure bucket and mapping brought the streaming
stack to fourteen deployed resources. The single approved replay acknowledged
884 unique events in 1,049.545 seconds. Full reconciliation verified 916 DynamoDB
items (884 results and 32 sender states), every sender version, all 884 S3 audit
payloads against DynamoDB, and all features/scores against the frozen reference.
The maximum absolute score difference was 2.9103830456733704e-11.

Monitoring showed zero runtime errors, record failures, Lambda Errors and
Throttles. The isolated failure prefix was empty. Both audit encryption samples
were AES256, and the isolated bucket's four public access blocks and AES256
encryption were verified. The renewed AWS login used the existing project IAM
identity. A reviewed shutdown plan changed only mapping enabled true to false;
AWS confirmed Disabled and the final Terraform plan had no differences.

All resources/evidence remain retained; no second replay or cleanup occurred.
The provisioned shard remains billable at USD 0.0184/hour. ECR's known 8 HIGH,
6 MEDIUM and 1 LOW findings remain unresolved; broader image reuse requires a
patch/rebuild and fresh review. See the [completed result](archive/reviews/streaming-demo-result-20261006.md)
and [machine-readable evidence](evidence/streaming-deployment-result-20261006.json).

## Final demonstration preparation

The [demo runbook](delivery/final-demo-runbook.md) now maps verified results to the
instructor's 15-minute presentation plus 5-minute Q&A slot. The
[slide content outline](delivery/final-presentation-outline.md) identifies proposal claims
that must change for the final implementation. Local validation checked all four
published-release file hashes, both recorded screenshot hashes, the full-score
verification hash and thirteen document links. This step used retained local
evidence and made no AWS calls, deployments, publications or paid runs.

[Readiness metadata](evidence/final-deliverable-readiness-20261006.json) explicitly leaves
the final deck, updated editable architecture, actual member contributions,
narrated setup/run videos and links, final access/submission checks, and approved
post-presentation cleanup outstanding. It does not claim those artefacts exist.

## Implemented architecture diagrams

Both editable diagrams now reflect the verified implementation: two core BQ1/BQ2
pages in `architecture/aml-pipeline-architecture.drawio`, and separate orchestration/streaming
pages in `architecture/aml-pipeline-aws-icons.drawio`. Original proposal sources are preserved
in `archive/architecture-proposal/`. Companion Markdown guides explain local/AWS
boundaries, catalog/query grain, failure-route semantics and limitations.

All four pages were exported locally using the signed official draw.io 31.7.0
portable application, whose archive SHA256 matched official release metadata.
Visual review corrected broken generic stencils and branch-label collisions.
The final PNGs in `architecture-exports/` embed XML whose cell IDs and labels match
the corresponding source page. XML references, official offline stencil names,
export script syntax and repository whitespace checks passed. See
[architecture validation](evidence/architecture-validation-20261006.json).

An attempted online preview was rejected by automatic approval review because it
would transmit private architecture content to diagrams.net. No further upload
was attempted; local export completed the requested work. No AWS mutations or
paid cloud runs occurred. The final deck, videos and confirmed contributions
remain pending.
