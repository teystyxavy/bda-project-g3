# AWS batch foundation runbook

Status: foundation deployed from `feature/aws-batch-foundation` on 2026-10-05.
See [deployment verification](aws-deployment-status.md). The approved 1,000-row
Glue smoke run and both Athena checks have now succeeded.
Terraform formatting, provider validation, and three mocked planning tests pass.
All 21 Python tests pass in the official Glue 5 container, including the 1,000-row
Spark/Parquet parity test and non-USD same-day/prior-day FX checks. The native
Windows Python suite still skips the Spark module; use the container runner for
the complete suite. An authenticated plan completed on 2026-10-05 with 24
additions and no changes/deletions; see [the saved-plan review](aws-plan-review.md).
That infrastructure plan has now been applied with user approval; the follow-up
plan reports no changes. The sample execution was separately approved and verified;
full-data execution remains pending approval.

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
remain future AWS work. Existing dashboards still consume local outputs.

The catalog uses an explicit schema and Athena date partition projection, so
this initial path needs neither a crawler nor partition repair. This differs
from the proposed crawler architecture intentionally: the input schema is known.
The failure-event rule only sends notifications; it does not schedule jobs or
implement the planned Step Functions enhancement.

## 1. Local preparation (no AWS calls)

Run in `bda-project-g3/`, using the documented Python environment:

```powershell
python -m pip install -e ".[dev]"
python scripts/prepare-aws-smoke.py
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
.\scripts\test-glue-local.ps1
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
`scripts/plan-aws.ps1 -AccountId YOUR_ACCOUNT_ID -AlertEmail YOUR_EMAIL` from the
repository root. It checks the connected account, retains existing budget/project
settings, and saves variables/state/plan outside the repository. It never applies.
Pass `-MonthlyBudgetUsd 5` to explicitly update an existing saved budget; otherwise
an existing value is preserved. Fresh setups default to USD 5 per month.

Choose the AWS account, region and alert recipient. Singapore (`ap-southeast-1`)
is the current configurable default. Authenticate through your approved AWS
profile, and check `aws sts get-caller-identity` before planning. Credentials
belong in the AWS credential mechanism, never this repository.

This checkout is under OneDrive. Store Terraform state, saved plans and private
variable files outside this synced checkout. The local backend is suitable for
one designated deployer only; coordinate team access before introducing shared
state. Losing state makes resource ownership and cleanup harder.

Example setup, after replacing all example values in your private vars file:

```powershell
$stateDir = Join-Path $env:LOCALAPPDATA 'IS459-G3'
New-Item -ItemType Directory -Force $stateDir
Copy-Item infra/terraform/terraform.tfvars.example "$stateDir/batch.tfvars"
notepad "$stateDir/batch.tfvars"
terraform -chdir=infra/terraform init -reconfigure "-backend-config=path=$stateDir/terraform.tfstate"
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
The default USD 5 monthly budget covers the whole account and sends notices at
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

The approved 2026-10-05 run uses `raw/full-20261005/` inputs and
`curated/full-20261005/transactions/` output, with 5,078,345 expected rows,
two G.1X workers, ten-minute timeout, and zero retries. Terraform manages the
separate `full_transactions` table. Its location is run-specific: future runs
must use a new output prefix and a reviewed catalog-location change, not append
another run under the existing table. Do not query until Glue is SUCCEEDED.

Run `sql/reconcile-full.sql` in the existing Athena workgroup. Download the
completed Parquet to a new local directory, then run (after installing the
local package as described in README):

```powershell
python scripts/verify-aws-full.py --curated output/full-20261005/curated --report output/full-20261005/parity.json
```

This checks every source ID for uniqueness and complete coverage, all daily
currency/bank corridor aggregates against the full local reference, and five
history features plus key transaction fields against the saved 256,557-row
sender cohort. It does not claim full-population model validation or scoring.
See the deployment-status document for actual results and run identifiers.

## 6. Cleanup after the presentation

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
