# Deploying the pipeline from zero


## Prerequisites

- AWS CLI v2 and Terraform installed. Every deploy script falls back to
  `output/tools/terraform/terraform.exe` if `terraform` isn't on PATH, so a
  vendored copy also works.
- A configured AWS profile. Run `scripts/aws/configure-aws.ps1` once — it
  signs you in (browser or SSO), creates a second `<profile>-tools` profile
  that Terraform/boto3 use, and writes `%LOCALAPPDATA%\IS459-G3\batch.tfvars.json`
  with your account ID, region and alert email. Every script below defaults
  to profile `is459-g3-tools` and region `ap-southeast-1`.
- Docker Desktop, for every stack that builds a container image
  (`sagemaker`/`ingestion`, `cloud-ingestion`, `streaming`).
- A Python environment with this repo's dependencies (`pip install -e ".[dev]"`),
  for the `prepare-*.py` scripts.
- The deploy/init scripts are PowerShell (`.ps1`); this tooling is written
  for Windows (one script connects to Docker Desktop over a named pipe).

All resources live in account `588738581042`, region `ap-southeast-1`
(Singapore).

## Deployment order


```
1. tfstate-bootstrap   (once, ever)
2. terraform            ("foundation")
3. hosting
4. sagemaker  ─┐
5. ingestion   ┘ (deployed together, in that order, by one script)
6. cloud-ingestion      (needs ingestion's state machine)
7. eventbridge-trigger  (needs ingestion's state machine)
8. dashboard-status     (needs ingestion's + cloud-ingestion's state machines)
9. dashboard-control    (same dependency as dashboard-status)
10. streaming           (separate demo; independent of 4-9, needs only 1-2)
```
---

## 1. infra/tfstate-bootstrap

**Once before deploying the rest of the stack.** If you're joining an existing
deployment (this project's account already has it).

**Resources**

| Resource | Name | Purpose |
|---|---|---|
| `aws_s3_bucket.state` | `is459-g3-tfstate-588738581042` | Holds every stack's `terraform.tfstate`, one object per stack under its own key (`foundation/terraform.tfstate`, `ingestion/terraform.tfstate`, etc.). Versioned — the safety net: every `apply` overwrites the state object in place, so this lets a bad push be rolled back to the previous object version. Encrypted at rest (SSE-S3/AES256), free. |
| `aws_dynamodb_table.lock` | `is459-g3-tfstate-lock` | State-locking table: one item per state key, written for the duration of a `plan`/`apply`/`destroy` and deleted when it finishes. |

Every other stack's backend config (`infra/shared.s3.tfbackend`) points at
both — `terraform apply` on any later stack has to acquire this DynamoDB
lock first, which is what prevents two concurrent applies from corrupting
shared state.

**Commands:**
```powershell
cd infra/tfstate-bootstrap
terraform init
terraform plan -var="aws_account_id=588738581042" -out=bootstrap.tfplan
terraform apply bootstrap.tfplan
cd ../..
```

---

## 2. infra/terraform ("foundation")

**Resources:**

| Resource | Name | Purpose |
|---|---|---|
| `aws_s3_bucket.pipeline` | `is459-g3-<account>-<region>` | Single data-lake bucket for the whole pipeline: `raw/` (Kaggle CSV + FX inputs), `curated/` (Parquet written by Glue), `scripts/` (Glue job code), `tmp/` (Spark scratch), `athena-results/`. Versioned + encrypted (SSE-S3/AES256). Public-access blocked and TLS-only bucket policy, since the dataset is financial-crime data. Lifecycle rules expire `tmp/` and `athena-results/` after 7 days. |
| `aws_glue_job.curate` | `is459-g3-curate` | Smoke-test ETL job: reads the 1,000-row sample in `raw/smoke/`, joins accounts and FX rates, converts amounts to USD, writes partitioned Parquet to `curated/smoke/transactions/`. Glue 5.0, 2×G.1X workers, 10-minute timeout, no retries (cost control). Backed by its own least-privilege IAM role (`aws_iam_role.glue`) and two CloudWatch log groups (`/is459-g3/glue/error`, `/is459-g3/glue/output`, 14-day retention). |
| `aws_glue_catalog_database.curated` | `is459_g3_curated` | Glue Data Catalog database holding the `smoke_transactions` and `full_transactions` table definitions (defined in `catalog.tf`) that Athena queries against. |
| `aws_athena_workgroup.analysis` | `is459-g3-analysis` | Workgroup used for all project queries (reconciliation and verification; BQ2 now runs as the Glue network job), plus its saved queries. Enforces workgroup configuration, caps any single query at 8 GiB scanned (`bytes_scanned_cutoff_per_query`), writes results to `athena-results/` (SSE-S3 encrypted). |
| `aws_sns_topic.failures` | `is459-g3-failures` | The one failure topic every stack in the project publishes to, plus an e-mail subscription to `var.alert_email` (confirmed via the link AWS e-mails on first apply). |
| `aws_cloudwatch_event_rule.failures` | — | EventBridge rule matching "Glue Job State Change" events for the curate job ending in `FAILED`, `TIMEOUT` or `STOPPED`, forwarded to the SNS topic via a narrowly-scoped delivery role (`aws_iam_role.failure_delivery`). |
| `aws_budgets_budget.account` | `is459-g3-account-monthly` | Monthly cost budget (`var.monthly_budget_usd`, capped at $10) with e-mail alerts at 50/80/100% of actual spend. |

The smoke job is **not a dependency for anything downstream** — nothing in
any later stack references it. It's a documented, optional acceptance test
(`planning/runbooks/aws-batch-runbook.md`) for this stack's own wiring
(bucket, IAM, Glue, Athena catalog, SNS alert path), run once by hand after
applying, not something `terraform apply` needs to succeed.

**Commands:**
```powershell
.\scripts\aws\plan-aws.ps1 -AccountId 588738581042 -AlertEmail you@example.com
# review the plan it saves to %LOCALAPPDATA%\IS459-G3\batch.tfplan, then:
cd infra/terraform
terraform apply "$env:LOCALAPPDATA/IS459-G3/batch.tfplan"
cd ../..
```
`plan-aws.ps1` only ever plans — it refuses to run with injected AWS
environment-variable credentials (so Terraform definitely uses the chosen
profile), confirms the signed-in account matches, and writes/updates
`batch.tfvars.json`. The `apply` is a separate, explicit step.

---

## 3. infra/hosting

**Resources (9):**

| Resource | Name | Purpose |
|---|---|---|
| `aws_s3_bucket.dashboard` + hardening (public-access block, ownership controls, encryption, versioning) | — | Private S3 bucket for dashboard releases: versioned, encrypted, ownership-controlled. |
| `aws_cloudfront_origin_access_control.dashboard` | — | Lets the bucket policy allow "this distribution only" rather than all of CloudFront. |
| `aws_cloudfront_response_headers_policy.dashboard` | — | Response-headers policy applied to the distribution. |
| `aws_cloudfront_distribution.dashboard` | — | The CloudFront distribution itself. |
| `aws_s3_bucket_policy.dashboard` | — | Bucket policy that ties the OAC to the bucket. |

Gated by two chained variables: `provision_hosting` creates the bucket/OAC
but not a public URL; `publish_dashboard` turns on the public CloudFront
endpoint, and its own validation refuses `true` unless `provision_hosting`
is also true **and** `public_demo_approved=true` — an explicit human
sign-off for public exposure and its (small) traffic cost.

**Commands:**
```powershell
scripts/aws/init-terraform.ps1 -Stack hosting -AllowEmptyState
cd infra/hosting
terraform plan -var="aws_account_id=588738581042" -var="provision_hosting=true" -out=hosting.tfplan
terraform apply hosting.tfplan
# When ready to expose it publicly:
terraform plan -var="aws_account_id=588738581042" -var="provision_hosting=true" -var="publish_dashboard=true" -var="public_demo_approved=true" -out=publish.tfplan
terraform apply publish.tfplan
cd ../..
```

---

## 4–5. infra/sagemaker + infra/ingestion

These two are deployed together, in this order, by one script, because
`infra/ingestion`'s control Lambda needs `infra/sagemaker`'s ECR repo to
already have an image pushed to it.

**infra/sagemaker resources (3):**

| Resource | Name | Purpose |
|---|---|---|
| `aws_ecr_repository.training` | — | Private, immutable-tag repo for the training container image. |
| `aws_iam_role.training` + its policy | — | The role SageMaker assumes to run a training job. |

There is **no SageMaker "instance" resource** — a SageMaker Training Job's
compute is a parameter of the `CreateTrainingJob` API call, chosen
dynamically per run by `run_profile(rows)` in
`src/bda_pipeline/ingest_contract.py`, not pre-provisioned infrastructure.
This stack only provides the two things a training job genuinely needs
ahead of time: somewhere to pull its image from, and a role to run as.

**infra/ingestion resources (exactly 41):**

| Resource | Name | Purpose |
|---|---|---|
| `aws_lambda_function.control` + IAM role/policy | `is459-g3-ingest-control` | Control Lambda (code in `src/bda_pipeline/ingest_control.py`, packaged as `control.zip`) that drives a run end-to-end. |
| `aws_glue_job.job` (×5) + shared IAM role | `curate`, `score`, `baseline`, `export`, `network` | `curate`/`score`/`network` run Spark, `baseline`/`export` run on G.2X; all five share one IAM role scoped to `pipeline/*` and `tmp/ingest/*`. |
| `aws_cloudwatch_log_group.glue` (×10) | — | Error + output log group per Glue job. |
| `aws_glue_catalog_database.runs` | `is459_g3_ingest` | Empty at apply time — the control Lambda creates one table per run (`run_<run_id>`) at runtime via `glue:CreateTable`, since the run ID doesn't exist until a run starts. |
| `aws_sfn_state_machine.ingest` + role | `is459-g3-ingest` | The ingestion Step Functions state machine. |
| `aws_cloudwatch_event_rule.cancel_workers` + Lambda permission + target | — | Cancels any still-running Glue jobs if an execution fails. |

Both this Glue role and this catalog database are **separate from
foundation's** (`is459-g3-glue` / `is459_g3_curated`) — different S3 prefix
scope (least privilege: this role has no access to `raw/`/`curated/`,
foundation's has no access to `pipeline/*`), and a fundamentally different
catalog purpose (static Terraform-defined tables vs. dynamic per-run ones).

The control Lambda's own IAM policy, by purpose:
- **Check run state** — read `dashboard/current.json`'s existence; read
  curated/network output success markers; read Glue catalog metadata
  (`GetDatabase`/`GetPartitions`).
- **Read pipeline state** — anything under `pipeline/*`, and the current
  dashboard pointer.
- **Write run outputs** — the run lock, stage-cache copies, curated/scored/
  network cache copies, run evidence, SageMaker training inputs, and the
  dashboard release files.
- **Release the lock** — `s3:DeleteObject` on exactly `pipeline/lock.json`,
  nothing else (the only delete permission in the policy).
- **Register results** — `glue:CreateTable`/`GetTable` on the per-run catalog.
- **Clean up on failure** — stop Glue job runs; stop/describe/list SageMaker
  training jobs (`ListTrainingJobs` needs `Resource: "*"` — that action has
  no resource-level ARN scope in AWS, not a broadened grant).
- **Manual recovery** — `states:DescribeExecution`.
- **Publish** — `cloudfront:CreateInvalidation` after a dashboard release.
- **Observability** — its own CloudWatch logs.

**Commands:**
```powershell
# 1. Build the training container image locally — no AWS calls
python scripts/aws/prepare-sagemaker-container.py --output output/ingestion-context
docker build --platform linux/amd64 --provenance=false -t is459-g3-training:local output/ingestion-context

# 2. Read the local image's digest (no push)
docker image inspect is459-g3-training:local --format '{{json .Descriptor}}'

# 3. Package deployment artifacts, pinning that digest (no AWS)
python scripts/aws/prepare-ingestion.py --output output/ingestion-artifacts `
    --models output/models --wheels output/runtime.gluewheels.zip `
    --image-uri 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@sha256:<digest-from-step-2>

# 4. Dry-run integrity check (hashes + digest only, no AWS writes)
.\scripts\aws\deploy-ingestion.ps1 -PreparedDirectory output/ingestion-artifacts -Image is459-g3-training:local

# 5. Real deployment
.\scripts\aws\deploy-ingestion.ps1 -PreparedDirectory output/ingestion-artifacts -Image is459-g3-training:local -Execute
```
Step 5, with `-Execute`, runs in this exact order and starts no pipeline run:
1. Re-verifies every prepared artifact's SHA-256 and that the local image
   digest matches what's pinned in `deployment.json`.
2. Runs a read-only AWS preflight (`scripts/ingestion/check-ingestion.py` —
   account, buckets, Athena guard, and the SageMaker `ml.m5` quota) and
   aborts if the quota isn't available.
3. Applies `infra/sagemaker` first (exactly 3 resources).
4. Pushes the training image to that now-existing ECR repo and verifies the
   remote digest matches.
5. Plans and applies `infra/ingestion`, refusing anything except an exact,
   all-`create` plan of exactly 41 resources.

---

## 6. infra/cloud-ingestion

**Resources (exactly 9):**

| Resource | Name | Purpose |
|---|---|---|
| `aws_ecr_repository.acquisition` + `aws_ecr_repository_policy.lambda` | — | ECR repo for the acquisition image; the repository policy lets only this Lambda pull images. |
| `aws_cloudwatch_log_group.acquisition` | — | Log group for the acquisition Lambda. |
| `aws_lambda_function.acquisition` + IAM role/policy | `is459-g3-acquire` | Container-image Lambda (code in `src/bda_pipeline/cloud_acquisition.py`) that pulls the Kaggle HI-Small dataset and FX rates directly from inside AWS — no laptop download needed. |
| `aws_sfn_state_machine.cloud_ingest` + IAM role/policy | `is459-g3-cloud-ingest` | Step Functions wrapper that starts and waits on `is459-g3-ingest` (step 5's state machine). On a result-cache hit it skips the batch: `ExistingRelease` invokes `ingest-control` (op `activate`) to verify the reused release and switch `dashboard/current.json` to it. |

This is why this stack has to come after ingestion: the dependency above is
a hardcoded ARN string, so applying this stack earlier would succeed but
leave a workflow that fails at runtime until ingestion exists.

**Commands:**
```powershell
# 1. Build context — Dockerfile, requirements.txt, and 6 acquisition-only
#    modules (cloud_acquisition, acquisition, dataset_options, ingest_contract,
#    fx, transform) — no datasets, credentials or training code
python scripts/aws/prepare-cloud-ingestion.py --output output/cloud-acquisition-context

# 2. Build the image locally — still nothing touches AWS
docker build --platform linux/amd64 -t is459-g3-acquisition:local output/cloud-acquisition-context

# 3. Dry-run integrity check (hash + digest match only)
.\scripts\aws\deploy-cloud-ingestion.ps1 -Prepared output/cloud-acquisition-context -Image is459-g3-acquisition:local

# 4. Real deployment
.\scripts\aws\deploy-cloud-ingestion.ps1 -Prepared output/cloud-acquisition-context -Image is459-g3-acquisition:local -Execute
```
Step 4, with `-Execute`: plans all 9 additions first purely to validate them,
applies only the 2 ECR foundation resources (repo + policy), pushes and
verifies the image, then plans and applies the remaining 7 (log group,
Lambda, both IAM role/policy pairs, the state machine). No acquisition run
or paid batch job is started.

HI-Large is cache-only in this stack: `Acquire` replays the pinned
`hi-large-delta1-20261107-batch` run and never starts the batch (see
[Run HI-Large from the cache](planning/runbooks/ingestion-runbook.md#run-hi-large-from-the-cache-free)).
The pin depends on `pipeline/deployment.json`, so a fresh `prepare-ingestion`
redeploy of step 5 makes that replay fail safely until the pin is updated.

---

## 7. infra/eventbridge-trigger

**Resources (exactly 8):**

| Resource | Name | Purpose |
|---|---|---|
| `aws_s3_bucket_notification.eventbridge` | — | Turns on EventBridge events for the data bucket. |
| `aws_cloudwatch_event_rule.raw_upload` | — | Matches an "Object Created" event on `pipeline/runs/*/raw/trigger.json`. |
| `aws_iam_role.events` + `aws_iam_role_policy.events` | — | Lets that rule invoke the wrapper state machine. |
| `aws_cloudwatch_event_target.workflow` | — | Event target wiring the rule to the wrapper state machine. |
| `aws_iam_role.workflow` + `aws_iam_role_policy.workflow` | — | Lets the wrapper start `is459-g3-ingest` and publish to the SNS failure topic. |
| `aws_sfn_state_machine.raw_trigger` | `is459-g3-raw-trigger` | The wrapper state machine itself. |

`scripts/ingestion/ingest.py --trigger eventbridge` writes `trigger.json`
**last**, after every raw file and `manifest.json`, so this never fires on a
half-uploaded run.

**Commands:**
```powershell
.\scripts\aws\deploy-eventbridge-trigger.ps1              # plan only
.\scripts\aws\deploy-eventbridge-trigger.ps1 -Execute      # apply
```
The script plan-checks that the change touches only these 8 resources with
only create/update actions, applies the saved plan, and starts no pipeline
run (`-Disable` pauses the rule without destroying anything; `-Destroy`
tears the stack down).

---

## 8. infra/dashboard-status

**Resources (10):**

| Resource | Name | Purpose |
|---|---|---|
| `aws_cloudwatch_log_group.function` | — | Log group for the status Lambda. |
| `aws_iam_role.function` + `aws_iam_role_policy.function` | — | Read-only: `states:ListExecutions`/`DescribeExecution`/`GetExecutionHistory` on both the ingestion and cloud-ingestion state machines — nothing else. |
| `aws_lambda_function.status` | `dashboard_status.handler` | The status Lambda function. |
| `aws_apigatewayv2_api.status` + `aws_apigatewayv2_integration.status` | — | HTTP API with CORS locked to the dashboard origin. |
| `aws_apigatewayv2_route.status` | `GET /status` | One route, no auth. |
| `aws_lambda_permission.api` | — | Lets API Gateway invoke the Lambda. |
| `aws_cloudwatch_log_group.api` | — | Access-log group for the API. |
| `aws_apigatewayv2_stage.status` | — | Deployment stage, throttled to 5 req/s. |

Public, read-only, no start/stop capability.

**Commands:**
```powershell
python scripts/aws/prepare-dashboard-status.py --output output/dashboard-status-package
scripts/aws/init-terraform.ps1 -Stack dashboard-status -AllowEmptyState
cd infra/dashboard-status
terraform plan -var="lambda_zip_path=output/dashboard-status-package/dashboard-status.zip" -out=status.tfplan
terraform apply status.tfplan
cd ../..
```

---

## 9. infra/dashboard-control

**Resources (12):**

| Resource | Name | Purpose |
|---|---|---|
| `aws_dynamodb_table.launch` | `is459-g3-dashboard-launch` | On-demand, TTL'd table tracking in-flight launch requests. |
| `aws_cloudwatch_log_group.function` | — | Log group for the control Lambda. |
| `aws_iam_role.function` + `aws_iam_role_policy.function` | — | Adds `states:StartExecution` on the cloud-ingestion machine and read/write on the launch table, on top of the same read access as dashboard-status. |
| `aws_lambda_function.control` | `dashboard_api.handler` | The control Lambda function. |
| `aws_apigatewayv2_api.control` + `aws_apigatewayv2_integration.control` | — | HTTP API with CORS allowing GET/POST/OPTIONS. |
| `aws_apigatewayv2_route.control` (×7) | — | Run list/create, single run, session bootstrap/exchange/logout — only `POST /session/bootstrap` requires `AWS_IAM` auth; the rest handle auth in application code. |
| `aws_lambda_permission.api` | — | Lets API Gateway invoke the Lambda. |
| `aws_cloudwatch_log_group.api` | — | Access-log group for the API. |
| `aws_apigatewayv2_stage.control` | — | Tighter-throttled stage (2 req/s) — this one can mutate state, unlike status. |
| `aws_cloudwatch_metric_alarm.errors` | — | Error alarm to the shared SNS topic. |

**Commands:**
```powershell
python scripts/aws/prepare-dashboard-api.py --output output/dashboard-control-package
scripts/aws/init-terraform.ps1 -Stack dashboard-control -AllowEmptyState
cd infra/dashboard-control
terraform plan -var="lambda_zip_path=output/dashboard-control-package/dashboard-api.zip" -out=control.tfplan
terraform apply control.tfplan
cd ../..
```

---

## 10. infra/streaming (separate demo)

Not part of the main batch pipeline — a separately gated, separate-state
demo that the frozen model can score transactions one at a time via Kinesis
instead of only in nightly batch. Everything defaults off; five chained
variable validations mean Terraform itself refuses to enable live
processing (`enable_consumer=true`) unless the image, the failure bucket,
the stream, and explicit `replay_approved=true` all already exist.

**Resources (20 blocks across 5 gates):**

| Gate | Count | Resources |
|---|---|---|
| `provision_streaming=true` | 7 (always cheap) | ECR repo, DynamoDB state table, log group, IAM role+policy, failure metric filter+alarm, ECR repository policy. |
| `provision_stream=true` | 1 (the only resource that bills while idle) | The Kinesis stream. |
| `image_uri != null` | 1 | The consumer Lambda. |
| Stream + image + a failure destination | 1 | The Kinesis→Lambda mapping (created regardless, only *enabled* when `enable_consumer=true`). |
| `provision_failure_bucket=true` | 3 | The isolated on-failure S3 bucket. |
| `provision_publisher=true` | 9 | Publisher Lambda, its log group, IAM role+policy, event-invoke config, a separate scheduler role+policy, the (initially disabled) schedule, and an error alarm. |

**Commands (provisioning steps only — building/pushing the image and
creating resources; starting a replay is a separate, explicitly-approved
operational step, not part of provisioning):**
```powershell
scripts/aws/init-terraform.ps1 -Stack streaming -AllowEmptyState

# 1. Base resources
cd infra/streaming
terraform apply -var="provision_streaming=true" -var="run_id=<run-id>"
cd ../..

# 2. Build & push the consumer image
python scripts/streaming/prepare-streaming-container.py --artifacts output/models --output output/streaming-build
docker build --platform linux/amd64 --provenance=false -t is459-g3-streaming:local output/streaming-build
# push to the ECR repo from step 1, record the digest

# 3. Create the function (still disabled)
cd infra/streaming
terraform apply -var="provision_streaming=true" -var="provision_stream=true" `
    -var="run_id=<run-id>" -var="image_uri=<repo>@sha256:<digest>"
cd ../..
```
---