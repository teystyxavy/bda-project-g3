# Infrastructure stacks

Each directory is an independent Terraform stack, with provider constraints,
a committed provider lock file and mock tests under `tests/`.

| Stack | Responsibility |
|---|---|
| `terraform/` | Foundation: private data bucket, Glue/catalog, Athena, monitoring and budget |
| `hosting/` | Private dashboard origin and public CloudFront distribution |
| `dashboard-control/` | Protected ingestion UI API and invited operator sign-in; deployed; invited AWS operator sign-in |
| `scoring/` | Frozen-model Glue inference |
| `sagemaker/` | On-demand candidate training foundation |
| `ingestion/` | Curate/score/analyse/verify/publish orchestration, stage cache and failure control |
| `cloud-ingestion/` | AWS Kaggle/FX acquisition Lambda and wrapper workflow |
| `orchestration/` | Earlier bounded scoring/corridor workflow retained for demonstrations |
| `dashboard-status/` | Public read-only pipeline status API |
| `eventbridge-trigger/` | Opt-in S3-triggered pipeline start |
| `tfstate-bootstrap/` | Existing shared state bucket and lock table |
| `streaming/` | Optional Kinesis replay, Lambda scoring, DynamoDB state and audit; stream parked (`provision_stream`) |

Follow the [ingestion runbook](../planning/runbooks/ingestion-runbook.md) and
stack-specific README files for variables and deployment order. Run commands from
the repository root. All 12 stacks use the [shared S3 backend](tfstate-bootstrap/README.md).
Use `scripts/aws/get-terraform-inputs.ps1 -Stack <name>` for deployed inputs on a
new machine. Real variables, caches and plans remain private local artifacts;
reviewed recovery state snapshots are in `handoff/terraform-state/`. Historical resources are retained until
explicit cleanup; the Kinesis stream is parked and optional schedules remain disabled.

For the existing ingestion stack, `scripts/aws/update-ingestion.ps1` checks a prepared
artifact directory and previews the allowlisted update; `-Execute` applies the saved
plan. `scripts/aws/deploy-ingestion.ps1` is the initial-deployment helper. Neither helper
starts a pipeline run. The updater refuses destructive replacements.
