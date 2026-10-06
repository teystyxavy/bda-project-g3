# Infrastructure stacks

Each directory is an independent Terraform stack, with provider constraints,
a committed provider lock file and mock tests under `tests/`.

| Stack | Responsibility |
|---|---|
| `terraform/` | Foundation: private data bucket, Glue/catalog, Athena, monitoring and budget |
| `hosting/` | Private dashboard origin and public CloudFront distribution |
| `dashboard-control/` | Protected ingestion UI API and invited operator sign-in; implemented locally, deployment pending |
| `scoring/` | Frozen-model Glue inference |
| `sagemaker/` | On-demand candidate training foundation |
| `ingestion/` | Curate/score/analyse/verify/publish orchestration, stage cache and failure control |
| `cloud-ingestion/` | AWS Kaggle/FX acquisition Lambda and wrapper workflow |
| `orchestration/` | Earlier bounded scoring/corridor workflow retained for demonstrations |
| `streaming/` | Optional Kinesis replay, Lambda scoring, DynamoDB state and audit; stream parked (`provision_stream`) |

Follow the [ingestion runbook](../planning/runbooks/ingestion-runbook.md) and
stack-specific README files for variables and deployment order. Run commands from
the repository root. Terraform state, real variable files and plans are private
local artifacts, not repository content. Historical resources are retained until
explicit cleanup; the Kinesis stream is parked and optional schedules remain disabled.

For the existing ingestion stack, `scripts/aws/update-ingestion.ps1` checks a prepared
artifact directory and previews the allowlisted update; `-Execute` applies the saved
plan. `scripts/aws/deploy-ingestion.ps1` is the initial-deployment helper. Neither helper
starts a pipeline run. The updater refuses destructive replacements.
