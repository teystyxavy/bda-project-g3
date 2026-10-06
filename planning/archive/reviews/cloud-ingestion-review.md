# AWS acquisition deployment review — 6 October 2026

The nine additions were approved and deployed on 6 October 2026; post-deployment and post-run plans report no changes. The 1,000-row smoke and full 5,078,345-row cloud runs passed native score parity, corridor reconciliation and public hashes. Full acquisition took 53.149 seconds and the complete flow 24 minutes 59.294 seconds. The dashboard selects `cloud-full-20261006-aws1`, using recorded Kaggle version 8. See [deployment evidence](../../evidence/cloud-ingestion-deployment-result-20261006.json). The earlier 33-resource deployment and its successful smoke/full results remain recorded in `../../evidence/ingestion-deployment-result-20261006.json`.

```text
Console or ingest.ps1 (run options only)
                  ↓
is459-g3-cloud-ingest (Step Functions)
                  ↓
is459-g3-acquire (Lambda container)
Kaggle HI-Small + historical FX APIs → verified S3 raw manifest
                  ↓
existing is459-g3-ingest
Glue → frozen scoring / optional SageMaker candidate → Athena
                  ↓
existing reconciliation → versioned dashboard → CloudFront
```

The launcher now defaults to the deployed cloud wrapper. It sends a small request and checks progress; dataset downloading, FX retrieval and raw uploads happen in AWS. Closing the terminal after submission does not stop acquisition or processing. The existing working path remains available with `-LocalAcquisition`. The previous `../../architecture/automated-ingestion.drawio` documents that verified local-acquisition predecessor; the flow above documents this revision.

## Scope and controls

Only `HI-Small_Trans.csv` and `HI-Small_accounts.csv` are downloaded, about 510 MB combined. Fresh downloads resolve and pin both files to one Kaggle version. S3 retains a versioned, hash-verified source cache; default runs reuse it, while `-Refresh` fetches again. `-DatasetVersion` selects an explicit version. Historical FX is queried for transaction currencies/dates and preceding-day coverage, preserving provider and observation dates and rejecting missing, invalid or stale rates. The September 2022 model/schema compatibility gate remains enforced.

Each run claims its raw location before acquisition and commits its manifest after verified uploads. A failed acquisition starts no Glue or SageMaker job. A successful identical processing manifest returns the existing release unless `-Rerun` is requested. API/SDK retries are bounded; the wrapper has no automatic acquisition or paid batch retry. Failed attempts retain sanitized acquisition evidence and require a new run ID. Live acceptance verified source API connectivity, anonymous Kaggle access, external FX retrieval from AWS and full acquisition duration.

The Lambda uses 3,072 MB memory, 4,096 MB temporary storage, a 900-second timeout and reserved concurrency one. It has no VPC/NAT gateway. The full invocation used 53.149 seconds, 55.022 billed seconds including initialization, and 1,215 MB maximum memory. This verifies HI-Small fits the configured function; larger subsets remain unsupported. See [Lambda limits](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html) and [temporary storage](https://docs.aws.amazon.com/lambda/latest/dg/configuration-ephemeral-storage.html).

Public Kaggle download succeeded without a configured secret in the live smoke. KaggleHub documents that authentication is optional for public resources, with exceptions for consent/authenticated resources. If needed, supply an **existing** Secrets Manager ARN containing either `{"api_token":"..."}` or `{"username":"...","key":"..."}`. Only its ARN enters Terraform; values are read at runtime and never enter workflow input, image, run reports or state. An ARN change needs its revised policy/plan reviewed before deployment. See [KaggleHub authentication](https://github.com/Kaggle/kagglehub).

## Exact deployment

Read-only Terraform plan: **9 additions, 0 updates, 0 deletions** in account `588738581042`, Singapore `ap-southeast-1`.

| Terraform resource | Purpose |
|---|---|
| `aws_ecr_repository.acquisition` | Immutable encrypted acquisition images |
| `aws_ecr_repository_policy.lambda` | Image pull restricted to this Lambda |
| `aws_cloudwatch_log_group.acquisition` | Acquisition logs, 14-day retention |
| `aws_iam_role.acquisition` | Lambda execution identity |
| `aws_iam_role_policy.acquisition` | Scoped S3/log access; optional exact secret ARN |
| `aws_lambda_function.acquisition` | Kaggle and FX acquisition |
| `aws_iam_role.workflow` | Wrapper workflow identity |
| `aws_iam_role_policy.workflow` | Invoke acquisition, wait for existing batch, failure SNS |
| `aws_sfn_state_machine.cloud_ingest` | Source-to-dashboard wrapper |

No existing stack or caller policy is changed. The wrapper waits for the existing Standard workflow using `states:startExecution.sync:2`, which returns JSON output; its permissions include only that child's execution scope and the documented managed EventBridge rule. See [nested workflow integration](https://docs.aws.amazon.com/step-functions/latest/dg/connect-stepfunctions.html).

Reviewed image:

```text
588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-acquisition@sha256:62f7cdbfccc8b85a37ff6dab7427e13ab38f34e3c2c803cfda1439b961692374
```

The Linux amd64 image uses the digest-pinned AWS Lambda Python 3.12 base and its supplied runtime interface client. Its eight-file build context excludes datasets, credentials, model artifacts and unrelated project files. See [AWS Python container images](https://docs.aws.amazon.com/lambda/latest/dg/python-image.html).

Private backend: `%LOCALAPPDATA%/IS459-G3/terraform-cloud-ingestion/`; actual state is `state/terraform.tfstate`, provider/backend metadata is `runtime/`. The read-only saved plan is `deployment.tfplan`. Prepared context and sanitized workflow validation are under ignored `output/cloud-acquisition-context-20261006/`.

The implemented helper verifies artifact hashes and the local image digest by default:

```powershell
.\scripts\deploy-cloud-ingestion.ps1
```

After explicit deployment approval, `-Execute` rechecks all nine additions, creates the two ECR resources, pushes/verifies the reviewed image, then applies the seven runtime resources. It starts no acquisition or batch job. Partial deployment requires state inspection and a revised plan; the helper does not reset state or blindly repeat a partial apply.

## Verification and acceptance

[Recorded source/image/plan evidence](../../evidence/cloud-ingestion-validation-20261006.json) keeps
this preparation separate from the earlier live batch results.

- 17 focused Python tests passed: input contracts, versioned source reuse, successful release cache, conditional claims, FX failure isolation, credentials restoration and cloud launcher behavior.
- Terraform formatting/validation and one mock test passed. AWS workflow-definition validation returned `OK` with no diagnostics.
- The actual Linux Lambda image passed a 10-row integration fixture with networking disabled and a read-only root filesystem. Provider/S3 fixture substitutes exercised the raw manifest path; this does not prove real AWS network access.
- PowerShell launcher/deployment helper parsed; the helper's default integrity check passed without cloud writes. Python sources compiled.

The approved acceptance sequence completed: deployed nine additions and the image, verified one cloud-acquisition 1,000-row smoke, then verified full HI-Small. Both passed source provenance, input manifests, child reconciliation, immutable public hashes and selected release. These runs used frozen scoring; previously verified candidate training remains available through `-Train` and was not repeated for this acquisition change. The full FX artifact independently passed hash, date coverage and freshness checks: 285 records for 15 currencies, 31 August–18 September 2022, from Frankfurter, supplementary Yahoo Finance daily closes and USD identity valuation. No run lock remains, and the successful full manifest is cached.

Acquisition adds bounded Lambda compute, temporary-storage usage, ECR/S3 storage and API requests to the existing per-run Glue/Athena/SageMaker processing costs. There is no provisioned acquisition worker or NAT gateway. Record actual durations and updated billing after the live runs; prior billing snapshots predate this addition. Include this separate module, image repository and source-cache prefixes in the final post-presentation cleanup inventory; do not clean up now.
