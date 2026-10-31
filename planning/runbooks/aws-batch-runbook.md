# AWS batch foundation runbook

Current cross-component demonstration and submission preparation is in
[the final demo runbook](../delivery/final-demo-runbook.md). The sections below retain the
foundation build history; later scoring, hosting, orchestration and streaming
results supersede earlier milestone-only status statements.

Status: foundation deployed from `feature/aws-batch-foundation` on 2026-10-05.
See [deployment verification](../aws-deployment-status.md). The approved 1,000-row
Glue smoke run and both Athena checks have now succeeded.
Terraform formatting, provider validation, and three mocked planning tests pass.
All 21 Python tests pass in the official Glue 5 container, including the 1,000-row
Spark/Parquet parity test and non-USD same-day/prior-day FX checks. The native
Windows Python suite still skips the Spark module; use the container runner for
the complete suite. An authenticated plan completed on 2026-10-05 with 24
additions and no changes/deletions; see [the saved-plan review](../archive/reviews/aws-plan-review.md).
That infrastructure plan has now been applied with user approval; the follow-up
plan reports no changes. The sample execution was separately approved and verified;
Full-data curation and the separately approved frozen-model scoring execution
have since succeeded; see [full scoring verification](../archive/reviews/full-cloud-scoring-review.md).

Verified on 2026-10-04: the actual job entry point also completed through
`spark-submit --master local[2]` in that container using the sample inputs,
`--expected_rows 1000`, and temporary `/tmp/curated-smoke` output. It printed
`CURATION_VERIFIED rows=1000` and exited successfully after the Parquet readback.
This verifies local execution, not S3 access, deployed IAM or Glue service setup.

## Scope and acceptance gate

This milestone implements sample ingestion → Glue curation → Athena checks.
The job preserves string identifiers, resolves duplicate Account headers by
position, joins normalized composite account keys, validates amounts/labels/FX,
and writes same-day and prior-day USD amounts as date-partitioned Parquet.
It rejects incomplete joins and row-count changes, and refuses existing output.

The first 1,000 input rows are a transformation sample, not complete sender
histories. They have no positive labels. Behaviour windows, stable transaction
IDs, baseline scoring, training, pattern enrichment and dashboard publishing
were outside this initial sample scope. Subsequent full curation/scoring and
public dashboard acceptance are recorded below; training and export remain local.

The catalog uses an explicit schema and Athena date partition projection, so
this initial path needs neither a crawler nor partition repair. This differs
from the proposed crawler architecture intentionally: the input schema is known.
The failure-event rule only sends notifications; it does not schedule jobs or
implement the planned Step Functions enhancement.

## 1. Local preparation (no AWS calls)

Run in `bda-project-g3/`, using the documented Python environment:

```powershell
python -m pip install -e ".[dev]"
python scripts/validation/prepare-aws-smoke.py
python -m pytest -q
```

The sample command reads existing data and FX downloads. It writes
`output/aws-smoke/{transactions.csv,accounts.csv,fx.csv,expected.parquet,expected.json}`.
It refuses an existing output directory; reuse a valid sample, or specify a
fresh `--output` directory. The default sample has 1,000 rows, USD total
1,644,403,430.34 and zero positives. The first rows happen to be USD; non-USD
and prior-day differences are covered by synthetic fixtures. Retain the input
FX CSV with its source and observation-date columns.

For Spark parity, use the official Glue 5 container. Docker Desktop must be
running with Linux containers. From the repo:

```powershell
.\scripts\validation\test-glue-local.ps1
```

The runner pins the tested Glue image digest and mounts the repository read-only.
It runs all Python tests, uses container temporary storage for generated data,
and removes the container on exit. The downloaded image remains cached locally.
No AWS credentials are mounted. The integration tests compare every curated
field against local reference data, read partitioned Parquet back, check duplicate
normalized account keys and missing prior-day FX, and verify different daily rates
with a Euro fixture. The first download is large; subsequent runs reuse it.
Warnings from Spark's bundled pandas integration are upstream deprecations.

Install Terraform >=1.7,<2 and AWS CLI v2 for deployment. This development
session used a checksum-verified portable Terraform 1.9.8 under the ignored
`output/tools/terraform/` folder. You can use its executable instead of
`terraform` below. The provider lock file pins the validated AWS provider.

```powershell
terraform -chdir=infra/terraform init -backend=false
terraform -chdir=infra/terraform fmt -check
terraform -chdir=infra/terraform validate
terraform -chdir=infra/terraform test
```

The three Terraform tests use `mock_provider "aws"` and `command = plan`.
They verify private storage and bounded job/query defaults, and reject an
invalid account ID or budget above USD 250. They make no AWS calls and are not
a replacement for reviewing a real authenticated deployment plan.

## 2. Review deployment configuration

For guided authentication, follow [local AWS login](aws-login.md). The helper
creates a named profile and `batch.tfvars.json` outside the repository. If using
that helper, use its JSON filename with `-var-file` in the commands below and
skip manually copying/editing the example variables file.

To save approved inputs and generate a plan in one command, run
`scripts/aws/plan-aws.ps1 -AccountId YOUR_ACCOUNT_ID -AlertEmail YOUR_EMAIL` from the
repository root. It checks the connected account, retains existing budget/project
settings, connects shared state, and saves private variables/plans outside the repository. It never applies.
Pass `-MonthlyBudgetUsd 10` to explicitly select the current alert amount;
otherwise an existing value is preserved. Fresh setups default to USD 10 per month.

Choose the AWS account, region and alert recipient. Singapore (`ap-southeast-1`)
is the current configurable default. Authenticate through your approved AWS
profile, and check `aws sts get-caller-identity` before planning. Credentials
belong in the AWS credential mechanism, never this repository.

Authoritative state is in the [shared, locked S3 backend](../../infra/tfstate-bootstrap/README.md).
Keep saved plans and private variables outside this OneDrive checkout. Use the
shared input helper on another machine to recover deployed settings/artifacts.

Example setup, after replacing all example values in your private vars file:

```powershell
$stateDir = Join-Path $env:LOCALAPPDATA 'IS459-G3'
New-Item -ItemType Directory -Force $stateDir
Copy-Item infra/terraform/terraform.tfvars.example "$stateDir/batch.tfvars"
notepad "$stateDir/batch.tfvars"
.\scripts\aws\init-terraform.ps1 -Stack foundation
terraform -chdir=infra/terraform plan "-var-file=$stateDir/batch.tfvars" "-out=$stateDir/batch.tfplan"
```

Review the plan before authorizing an apply. It creates one private, versioned,
SSE-S3 bucket, two service roles with scoped policies, a Glue job, catalog/table,
Athena workgroup and saved queries, log groups, a failure notification path, and
a budget. It does not upload the dataset or start the Glue job.

The Athena operator uses their own approved permissions for workgroup queries,
catalog reads, curated object reads and query-result writes; Terraform does not
grant them to your login. A deployment role also needs IAM PassRole and resource
management permissions. Course account/SCP/Lake Formation restrictions may need
an administrator's configuration; do not compensate with blanket admin policies.

Cost controls: two G.1X workers, ten-minute job timeout, no automatic retries,
one concurrent run, 1 GiB per-query Athena scan cutoff, 14-day log retention,
and seven-day temporary/result expiry. A maximum-duration job allocates roughly
0.333 DPU-hours; multiply by the current regional Glue rate, then add storage,
queries, logs and notification costs. No current dollar estimate is asserted.
The default USD 10 monthly budget covers the whole account and sends notices at
50%, 80% and 100%. It is an alert, not a spending stop, and resets monthly.
Track cumulative course spend separately against the total USD 250 allowance.

## 3. Approved deployment and smoke run

These commands create resources, upload data and incur charges. Execute only
after the user explicitly approves deployment, as required by workspace AGENTS.md.

```powershell
terraform -chdir=infra/terraform apply "$stateDir/batch.tfplan"
$bucket = terraform -chdir=infra/terraform output -raw bucket_name
$job = terraform -chdir=infra/terraform output -raw glue_job_name
aws s3 cp output/aws-smoke/transactions.csv "s3://$bucket/raw/smoke/transactions.csv"
aws s3 cp output/aws-smoke/accounts.csv "s3://$bucket/raw/smoke/accounts.csv"
aws s3 cp output/aws-smoke/fx.csv "s3://$bucket/raw/smoke/fx.csv"
$runId = aws glue start-job-run --job-name $job --query JobRunId --output text
aws glue get-job-run --job-name $job --run-id $runId --query JobRun
```

Ensure the AWS CLI region matches Terraform's region (set it in the selected
profile or pass `--region` to each AWS command). Confirm the SNS subscription
email. Inspect the returned job status and configured log groups until terminal;
record the run ID, duration and `CURATION_VERIFIED rows=1000` message.

An existing `curated/smoke/transactions/` prefix causes a rerun to fail rather
than mix outputs. If a run fails after writing, preserve its evidence, then use
a fresh output prefix via job arguments and point a separate catalog table at
it. Do not clear the old prefix just to retry without cleanup authorization.

## 4. Athena verification

Select the Terraform-created workgroup and catalog database in Athena. Execute
the saved `smoke-reconciliation` query, then `smoke-corridors`. Compare with
`output/aws-smoke/expected.json`: row count and label count must match exactly;
both missing counts must be zero; USD totals must agree within USD 0.01 (floating
point summation). Corridor counts and volume must sum back to the same totals.
No `MSCK REPAIR TABLE` is required because date partitions are projected.

Capture sample files' hashes, Terraform plan, job logs/status, query results and
cost evidence for the project demonstration. Test the failure notification path
with a deliberately incorrect expected row count only in an approved smoke run;
confirm the email and ensure no new curated data appears for that failed run.

Do not infer full-data or AML model quality from this sample. Before scaling,
implement stable row IDs and exact behaviour-window parity, then full-history
scoring and dashboard export. A full run needs its own raw and curated prefixes,
expected row count, catalog table, and measured timeout/worker sizing.

## 5. Full-data run and verification

The extended job preserves physical source row IDs from one immutable CSV
(`HI-Small:<zero-based row>`). It rejects directory input. Keep the original
one-record-per-line source and record its SHA256; IDs are not stable across
reordered or changed source files. Set `--behaviour true` to compute exact
1h/24h/7d velocity and 24h/7d distinct-counterparty windows. All same-timestamp
peers are excluded and bank keys are normalized for history calculations.

The successful optimized 2026-10-05 retry uses `raw/full-20261005/` inputs and
`curated/full-optimized-20261005/transactions/` output, with 5,078,345 expected rows,
two G.1X workers, ten-minute timeout, and zero retries. Terraform manages the
separate `full_transactions` table. Its location is run-specific: future runs
must use a new output prefix and a reviewed catalog-location change, not append
another run under the existing table. Do not query until Glue is SUCCEEDED.

Run `sql/reconcile-full.sql` in the existing Athena workgroup. Download the
completed Parquet to a new local directory, then run (after installing the
local package as described in README):

```powershell
python scripts/validation/verify-aws-full.py --curated output/full-cloud-optimized-20261005/curated --report output/full-cloud-optimized-20261005/parity-recheck.json
```

This checks every source ID for uniqueness and complete coverage, all daily
currency/bank corridor aggregates against the full local reference, and five
history features plus key transaction fields against the saved 256,557-row
sender cohort. It does not claim full-population model validation or scoring.
See the deployment-status document for actual results and run identifiers.

## 6. Separate frozen-model scorer (local validation complete)

The curation job remains unchanged. `jobs/score_transactions.py` is now locally
tested in the pinned Glue 5 container: 44 tests pass, including a 1,024-row
distributed inference/Parquet round-trip comparison with the frozen native model.
See [scoring preparation and validation](../archive/reviews/cloud-scoring-preparation.md) for commands,
Linux wheel packaging, trusted hashes and the XGBoost runtime-version difference.
The separate scoring stack and both approved artifact releases are deployed.
The localization retry scored all 1,024 rows with exact native parity but FAILED
at output commit. Exact Glue job/account trust worked. Four partial Parquet files
are retained for diagnosis, with no success marker. See
[smoke and retry history](../archive/reviews/scoring-infrastructure-review.md). The third smoke also
FAILED with exact parity; root-marker permissions were insufficient. See
[current commit review](../archive/reviews/scoring-success-marker-review.md) for the tested explicit
success-marker setting. The approved fourth run **SUCCEEDED** in 120 execution
seconds, with a real S3 `_SUCCESS` and ordinary 1,024-row reconciliation showing
zero score difference. Two updates applied, zero uploads; the post-apply plan
showed no changes. Another paid run needs its own approval. Do not rerun curation to test
scoring, overwrite original artifacts, or treat the failed prefix as validated.

The successful run's Parquet was downloaded privately to
`C:/Users/lowth/AppData/Local/IS459-G3/scoring-success-verification/curated`.
Verification already passed and its report exists. To repeat only local
reconciliation, choose a fresh report filename (existing reports are protected):

```powershell
output/ml-venv/Scripts/python.exe scripts/validation/verify-cloud-scoring-smoke.py --curated C:/Users/lowth/AppData/Local/IS459-G3/scoring-success-verification/curated --package output/cloud-scoring-retry-prepared-20261005 --run-report C:/Users/lowth/AppData/Local/IS459-G3/scoring-success-status.json --report output/scoring-smoke-success/parity-repeat.json
```

For future approved runs, also verify the run's `_SUCCESS` object exists in S3;
never create it manually. The verifier
requires SUCCEEDED and checks count/IDs, features, scores, date partitions and
cohort queues; locally reconstructed explanations are identified explicitly.
Preserve failed-run evidence and require approval before a full-data run.
For diagnosis only, `--forensic` allows FAILED reports and explicitly returns
`pipeline_passed=false`; it never promotes a partial prefix. The saved
`output/scoring-localized-forensics-20261005/forensic-parity.json` checks all
1,024 partial rows, features, partitions and top-100 order. Explanations were
reconstructed locally. Five verifier tests cover the success gate and diagnostic
mode; the failed prefix must never be reused or published.

The full-data scoring path is implemented and verified in
[full scoring and local dashboard export](../archive/reviews/full-cloud-scoring-review.md), including
the exact private plan, source evidence, cost scenario and real verification/
export commands. It reuses the 131 verified curated Parquet objects and existing
artifact release. The approved plan applied with two updates and zero uploads;
the one authorized full run succeeded in 205 execution seconds. All 5,078,345
rows passed verification, and the local cloud-backed dashboard/release are built.
The verifier report is `output/scoring-full-verification-20261006/verification.json`;
downloaded Parquet remains privately outside OneDrive. A separately approved
hosting step then published the four verified assets; see the next section.
Full verification requires the captured job settings and a success-marker
receipt tied to the exact job run and output bucket/key. Never substitute the
smoke output or smoke marker for full-population verification.

## 7. Verified dashboard publication

Public static demo: [d1n61tlw9pcrog.cloudfront.net](https://d1n61tlw9pcrog.cloudfront.net/).
Separate hosting state: `C:/Users/lowth/AppData/Local/IS459-G3/hosting.tfstate`;
separate provider working directory: `C:/Users/lowth/AppData/Local/IS459-G3/terraform-hosting`.
Never point `infra/hosting/` at the batch/scoring state, and do not plan with
default `provision_hosting=false` against this deployed stack: that would propose
destruction. Current approved variables are account `588738581042`, region
`ap-southeast-1`, and all three hosting/publication flags true.

The approved staging plan added nine resources. Exactly four manifest-verified
runtime files were uploaded with declared types, AES256 and cache controls.
A reviewed single `enabled=false` to `true` change enabled distribution
`EU1WB6XM4GKHQ`; the post-apply plan showed no changes. Public hashes/security
headers, HTTP-to-HTTPS redirect, direct S3/unpublished-path denial and live
dashboard interactions passed. State, versioned upload receipts and deployment
logs remain private; screenshots and summaries are local.
See [publication review](../archive/reviews/dashboard-publication-review-20261006.md) and
[result metadata](../evidence/dashboard-publication-result-20261006.json).
The packaged manifest remains the immutable historical pre-publication record.
Future uploads or infrastructure changes need their own reviewed approval scope.

## 8. Verified orchestration — one approved execution completed

See [orchestration review](../archive/reviews/orchestration-review-20261006.md). Source and local
tests are implemented and the approved deployment/execution completed. Three
resources were added; only the existing scorer's output/temp arguments and
matching IAM prefixes were updated. Both post-apply plans show no changes.
All 5,078,345 scores and 1,082,185 daily corridors passed reconciliation.
No schedule is provisioned. See `infra/orchestration/README.md` for read-only
inspection commands suitable for recording a demonstration without replaying data.

Local preparation (use a fresh directory):

```powershell
python scripts/aws/prepare-orchestration.py --run-id full-orchestrated-20261006 --output output/orchestration-review-local
python -m pytest tests/test_orchestration.py -q
```

Use separate private `orchestration.tfstate` and provider working directory.
For a future run, obtain explicit approval, refresh/review both plans, apply the scorer prefix
updates and workflow resources, and check drift. Capture approved input and
execute once with the reviewed run ID as the execution name. Never set the
input approval flag before the user authorizes the specific run. The fixed name
and fresh-prefix checks prevent accidental replay; failures preserve evidence.
Verify live job/query status, the real marker, every scored row and all treasury
aggregates. A successful state machine only means outputs are ready for that
reconciliation. Publication remains a separate review.

Optional Scheduler stays absent unless specifically requested; defaulting an
already-deployed stack to `provision_orchestration=false` would plan destruction.
Use the deployed stack's actual approved variables for future drift checks.

## 9. Cleanup after the presentation

Cleanup needs separate explicit approval. First inventory this Terraform
state's resources and export evidence you need to retain. Review a destroy plan.
The bucket has `force_destroy=false`: destroy will refuse a nonempty bucket.
After approval, remove only the verified project bucket's objects, versions,
delete markers and incomplete uploads, then destroy the reviewed resources.
Never delete broadly by account or name pattern. Verify resource removal and
subsequent billing; retain state securely until cleanup is confirmed.

## Implementation references

- [Glue runtime versions](https://docs.aws.amazon.com/glue/latest/dg/release-notes.html)
- [Glue 5 logging and custom log prefixes](https://docs.aws.amazon.com/glue/latest/dg/monitor-continuous-logging.html)
- [EventBridge target permissions and execution roles](https://docs.aws.amazon.com/eventbridge/latest/userguide/eb-use-resource-based.html)
- [Local Glue container testing](https://docs.aws.amazon.com/glue/latest/dg/develop-local-docker-image.html)
