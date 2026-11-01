# Automated ingestion runbook

**1 November 2026 deployment verified:** baseline verification,
rule scoring and dashboard export now use Glue jobs `is459-g3-ingest-baseline`
and `is459-g3-ingest-export`. SageMaker is used only for optional candidate
training. The fresh 1,000-row AWS smoke passed through public publication with
all four Glue jobs successful. Earlier acceptance records below describe the predecessor.
See [migration and validation](glue-stage-migration.md) for the deployment evidence and full-population validation limits.


**Full-pipeline optimisation:** the deployed workflow now runs source/baseline
verification, frozen scoring and corridor analysis concurrently. Glue jobs use
synchronous completion; only optional SageMaker training uses bounded status checks. Successful curated/scored outputs can be
reused under semantic cache keys, while every new release retains the complete
independent verification gates. `-Rerun` bypasses all processing caches. See the
[implementation and recovery details](pipeline-optimisation-implementation.md)
and [measured results](../evidence/pipeline-optimisation-result-20261006.json).

**Cloud acquisition revision is deployed and verified through smoke/full publication.** The default command now
targets `is459-g3-cloud-ingest`, which will run Kaggle/FX acquisition in Lambda before
the existing batch workflow. Review the [nine-resource deployment](../archive/reviews/cloud-ingestion-review.md).
Add `-LocalAcquisition` to the run examples below to use the previous verified flow.
See [current deployment evidence](../evidence/cloud-ingestion-deployment-result-20261006.json).

The 33 reviewed resources are deployed. The 1,000-row AWS smoke completed on
6 October 2026, including verification and publication. The full 5,078,345-row
run with candidate training also passed and published; see [deployment evidence](../evidence/ingestion-deployment-result-20261006.json).
Running an uncached batch starts paid AWS jobs. The earlier smoke/full approval and
results apply to the completed local-acquisition implementation; live acceptance of
this new AWS acquisition revision was separately approved and both runs passed.

## Setup

From `bda-project-g3`, install the project and AWS launcher dependencies in your
Python environment:

```powershell
python -m pip install -e ".[cloud]"
```

Use the existing AWS setup helper `scripts/aws/configure-aws.ps1`. Cloud mode needs no
local dataset, FX file or Kaggle credential; it uses AWS-side source access and a
versioned S3 cache. If Kaggle requires authentication, configure an existing Secrets
Manager ARN as described in the review. Configure local Kaggle credentials only
for `-LocalAcquisition` or `-PrepareOnly`; valid local files are reused in those modes.
For those local modes, install `.[ingest]` instead; `.[ml,dev]` is only needed for
local modelling/tests, not for starting the cloud workflow.
The wrapper chooses `output/ml-venv/Scripts/python.exe` when present; use `-Python`
to select your own environment. Account and region are checked before AWS work.

## Run after cloud acquisition deployment

```powershell
# Complete HI-Small acquisition â†’ Glue â†’ Athena/model verification â†’ dashboard.
.\scripts\ingestion\ingest.ps1

# Same flow, additionally train a private candidate in SageMaker.
.\scripts\ingestion\ingest.ps1 -Train

# Approved small cloud smoke run; publishes a clearly labelled 1,000-row release.
.\scripts\ingestion\ingest.ps1 -Rows 1000

# Return after AWS accepts the workflow; processing continues in AWS.
.\scripts\ingestion\ingest.ps1 -NoWait
.\scripts\ingestion\ingest.ps1 -Status <run-id>

# Force a fresh run when the same inputs already have a successful release.
.\scripts\ingestion\ingest.ps1 -Rerun

# Compatibility path: local acquisition/upload, existing deployed AWS batch.
.\scripts\ingestion\ingest.ps1 -LocalAcquisition
```

The command reports the run ID, AWS execution link, progress, root dashboard link and
immutable release link. An unchanged successful manifest avoids new paid work and returns
the earlier release. Root dashboard data can differ if another run has since published.
`-Train` rejects `-Rows`: chronological training needs the complete subset and both
classes in training/validation. Candidate selection is frozen before test evaluation.
The candidate is verified and retained privately; it does not automatically replace
the active frozen model or its public metrics.

## Input controls

```powershell
.\scripts\ingestion\ingest.ps1 -DatasetVersion 1
.\scripts\ingestion\ingest.ps1 -Refresh
.\scripts\ingestion\ingest.ps1 -LocalAcquisition -FxFile <verified-historical-rates.csv>
```

`-DatasetVersion` must name an actual Kaggle version; `1` above is an example, not a
claim about the version of your existing files. A fresh download resolves the KaggleHub
cache version and pins both files to it. Cloud mode reuses verified versioned files
from S3; `-Refresh` requests a fresh Kaggle download. Legacy local files without a
recorded version remain honestly unversioned in the compatibility path.
Only HI-Small and the reviewed September 2022 model/schema contract are supported by
the cloud entry point. Use `-DownloadOnly` for all six subsets, optional pattern
descriptions or individual files. `-Size Medium` or `-Variant LI` without
`-DownloadOnly` is rejected before AWS calls. See the
[Kaggle download options](kaggle-download-options.md) for examples and costs.

Historical FX is retrieved by default for every transaction currency/day plus the
preceding day, with bounded API retries and a seven-day freshness limit. Missing,
future, stale, duplicate or invalid rates stop preparation. Explicit `-FxFile` supports
offline replay in local mode and is subject to the same checks. `-FxFile` and
`-Deployment` are rejected in default cloud mode. No batch compute job starts if
acquisition fails. Cloud raw inputs/manifests live under `pipeline/runs/<run>/raw/`
in the project bucket; local preparation remains under ignored `output/ingest/<run>/`.

You can also start `is459-g3-cloud-ingest` directly in the AWS console with
`{"run_id":"cloud-demo-unique-id"}`. Optional input fields are `rows`,
`dataset_version`, `train`, `refresh` and `rerun`; credentials are never input fields.
The default is full HI-Small with frozen scoring. Use a new run ID for each attempt.

## Start a run by uploading to S3 (EventBridge, opt-in)

Status: deployed on 6 October 2026; live acceptance passed on 7 October 2026.
Run `evb-smoke-20261007-135938` processed and published 1,000 rows. Re-uploading
its trigger marker created a second wrapper execution that ended through
`AlreadyStarted`; only one batch execution existed. All scores matched the
independent reference exactly, and corridor totals reconciled within tolerance.
The preceding dashboard release was restored after acceptance. See
[evidence](../evidence/eventbridge-trigger-result-20261007.json).

This verifies the opt-in upload route, not scheduled ingestion or every failure
mode. The target still has no dead-letter queue or dedicated delivery-failure
alarm; SNS in the wrapper cannot report failures that prevent the wrapper from
starting. The smoke used existing local files and historical FX; it does not
establish fresh Kaggle/API acquisition or a full-population EventBridge run.

This is an extra way to start the same batch. The default command above and
`is459-g3-cloud-ingest` still start the batch themselves.

```powershell
.\scripts\ingestion\ingest.ps1 -LocalAcquisition -Trigger EventBridge -Rows 1000
```

1. The laptop uploads the transactions, accounts and FX CSVs plus `manifest.json` to
   `pipeline/runs/<run>/raw/`, then writes `raw/trigger.json` last.
2. S3 sends an "Object Created" event to EventBridge. Rule
   `is459-g3-raw-upload-trigger` matches only `pipeline/runs/*/raw/trigger.json`.
3. Step Functions `is459-g3-raw-trigger` checks the key and starts `is459-g3-ingest`
   with an execution named after the run ID. A repeated S3 event finds that execution
   and stops, so it cannot start a second paid run. It does not retry, and it notifies
   `is459-g3-failures` on failure.
4. The command waits for the batch to start and follows it like a direct run.

Manage the trigger from the repository root (plan only unless `-Execute` is given):

```powershell
.\scripts\aws\deploy-eventbridge-trigger.ps1                    # plan only
.\scripts\aws\deploy-eventbridge-trigger.ps1 -Execute           # apply
.\scripts\aws\deploy-eventbridge-trigger.ps1 -Disable -Execute  # pause the rule
.\scripts\aws\deploy-eventbridge-trigger.ps1 -Destroy -Execute  # remove the stack
```

Cost is effectively zero: S3-to-EventBridge delivery and rule matching are free, and
the wrapper uses about five Standard state transitions per run. See
[the stack README](../../infra/eventbridge-trigger/README.md).

## Local preparation without deployment or paid jobs

```powershell
.\scripts\ingestion\ingest.ps1 -PrepareOnly -Rows 1000 `
  -Deployment output/ingestion-artifacts-20261006-v5/deployment.json
```

This may contact Kaggle/FX APIs, but uploads nothing and starts no AWS workflow. For
fully offline preparation add `-FxFile` and reuse a valid local dataset. Output locations
must be new; an explicit `-RunId` cannot overwrite an existing local preparation.

## Initial deployment (completed on this account)

The initial deployment below already completed on this account. Do not repeat
`-Execute` against deployed state: its guard intentionally requires exactly 33 new
resources. Subsequent updates need a reviewed Terraform change plan.

The prepared image/artifacts and private plans were built on 6 October 2026. First run
the integrity check:

```powershell
.\scripts\aws\deploy-ingestion.ps1
```

After explicit deployment approval:

```powershell
.\scripts\aws\deploy-ingestion.ps1 -Execute
```

It checks hashes/image, validates AWS prerequisites, plans and applies the three-resource
SageMaker foundation, pushes the immutable CPU image, verifies its remote digest, then
plans and applies the thirty-resource ingestion stack. It rejects mutations or a
different number of additions. It starts no processing/training job. State and plans
remain in `%LOCALAPPDATA%/IS459-G3/terraform-{sagemaker,ingestion}/`. Actual state is
in `state/terraform.tfstate`, separately from backend metadata/providers in `runtime/`.
A partial deployment
requires state inspection and a revised review rather than automatic reset.

For a clean machine, build a fresh allowlisted image context and prepare its artifact
set; adjust the deployment script's preparation directory/image parameters:

```powershell
python scripts/aws/prepare-sagemaker-container.py --output output/new-ingestion-context
docker build --platform linux/amd64 --provenance=false --sbom=false `
  -t is459-g3-ingestion:new output/new-ingestion-context
# Read the built Descriptor.digest; use its Group 3 ECR repository@sha256 URI below.
python scripts/aws/prepare-ingestion.py --output output/new-ingestion-artifacts `
  --models <verified-frozen-artifacts-directory> `
  --wheels <verified-runtime.gluewheels.zip> --image-uri <digest-pinned-ECR-URI>
```

The offline Glue wheels must remain compatible with Glue 5.0/Python3.11, independently
of the Python3.12 CPU container. Verify the frozen manifest before preparing. No input
dataset, credential, private state, or unrelated workspace directory enters an artifact.

## Failed execution and recovery

Inspect the execution and its `pipeline/runs/<run>/evidence/failure.json`. Outputs and
the previous selected release remain available. Paid jobs are not retried automatically.
Do not repeat the same run ID or manually delete its lock while workers are active.

```powershell
.\scripts\ingestion\ingest.ps1 -Status <failed-run-id>
.\scripts\ingestion\ingest.ps1 -Unlock <failed-run-id>
```

Unlock performs a recovery check: unsuccessful terminal workflow, owned lock, and no
active Glue/SageMaker jobs. It deletes only that lock and preserves all outputs. It
refuses success, running workflows, other owners, or active workers. Then use a fresh
run ID for an explicitly approved retry. If publication committed before a later
error, inspect `current.json` and the verified release before choosing rollback/retry.
Manual rollback and destructive cleanup require their own explicit approval.

For acquisition-stage failure, inspect the wrapper and
`pipeline/runs/<run>/evidence/acquisition-failure.json`. Before a batch child starts,
there is no batch lock to unlock. Lambda hard timeouts may precede evidence upload;
wrapper history and acquisition logs still identify the stopped stage. Retain the
claim/partial files and retry with a fresh run ID after resolving the cause.

## Validation evidence

`planning/evidence/ingestion-implementation-validation-20261006.json` records local tests,
container reconciliation and plan counts. `planning/evidence/ingestion-aws-preflight-20261006.json`
records read-only quota, price and AWS workflow-validation results. These files do not
claim a live AWS ingestion run. Actual smoke job/proof/publication records are saved
in ignored `output/ingestion-smoke-aws-20261006/` and
`output/ingestion-full-aws-20261006/`, summarized in the deployment result. Both
Terraform post-run plans report no changes. Public file hashes, root shell/pointer,
candidate period metrics and the successful cache path were independently checked.

`../evidence/cloud-ingestion-validation-20261006.json` separately records the new acquisition
image, source hashes, local checks and read-only nine-resource plan. It is preparation
evidence and does not claim a live Lambda acquisition run.

The later `../evidence/cloud-ingestion-deployment-result-20261006.json` records actual AWS
acceptance: `cloud-smoke-20261006-aws1` (1,000 rows) and
`cloud-full-20261006-aws1` (5,078,345 rows), both published with verified public
hashes and exact native score parity. Full acquisition used Kaggle version 8's
verified S3 cache and freshly fetched 285 FX records for 15 currencies. Its Lambda
duration was 53.149 seconds; the complete wrapper took 24 minutes 59.294 seconds.
The current selected release is the full run; global run lock is absent. Training
was not repeated for these acquisition acceptance runs. Read-only post-run Terraform
plan reports no changes. Evidence is retained under ignored
`output/cloud-acquisition-{smoke,full}-20261006/`.

For read-only operational verification:

```powershell
python scripts/ingestion/inspect-ingestion.py --run-id <run-id> --output output/run-progress
python scripts/validation/verify-ingestion-release.py --run-id <run-id> --output output/run-progress
```

Use the project Python environment configured above. `verify-ingestion-release.py`
also checks whether another run has since become current; immutable releases remain
verifiable. An already-open browser can retain its previous cached script: use the
reported versioned release link or a hard refresh when checking a newly selected run.

## Live dashboard operator controls

The deployed [Ingestion tab](https://d1n61tlw9pcrog.cloudfront.net/) can start and track cloud runs using the existing Group 3 AWS identity. With the project Python environment activated, run:

```powershell
python scripts/aws/dashboard-signin.py --profile is459-g3-tools
```

The helper verifies g1team3 and opens a single-use sign-in link; no email invitation or separate dashboard password is needed. If expired, restore the AWS session with `aws login --profile is459-g3` first. See the [control deployment and security guide](../../infra/dashboard-control/README.md).

The dashboard exposes HI-Small demo/custom/full row selection, latest or pinned dataset version, refresh, stage rebuild, full-run candidate training, configuration preview and real workflow progress. Local download-only choices and administrative recovery remain script operations. Starting new processing can incur AWS charges. Signing out revokes dashboard access but does not stop a running workflow.
