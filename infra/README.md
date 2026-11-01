# Infrastructure stacks

Each directory is an independent Terraform stack, with provider constraints,
a committed provider lock file. Application stacks have mock tests under `tests/`.

| Stack | Responsibility |
|---|---|
| `terraform/` | Foundation: private data bucket, Glue/catalog, Athena, monitoring and budget |
| `hosting/` | Private dashboard origin and public CloudFront distribution |
| `archive/` | Private archive for older coursework buckets; separate from live data, dashboard and state |
| `dashboard-control/` | Protected ingestion UI API and invited operator sign-in; deployed; invited AWS operator sign-in |
| `scoring/` | Retired standalone inference stack; current scoring runs in `ingestion/` |
| `sagemaker/` | On-demand candidate training foundation |
| `ingestion/` | Deployed curate/score/baseline/export Glue jobs, Athena orchestration, optional training, publication and failure control |
| `cloud-ingestion/` | AWS Kaggle/FX acquisition Lambda and wrapper workflow |
| `orchestration/` | Retired predecessor workflow; current orchestration runs in `ingestion/` |
| `dashboard-status/` | Public read-only pipeline status API |
| `eventbridge-trigger/` | Opt-in S3-triggered pipeline start |
| `tfstate-bootstrap/` | Existing shared state bucket and lock table |
| `streaming/` | Optional Kinesis replay, Lambda scoring, DynamoDB state and audit; stream parked (`provision_stream`) |

Follow the [ingestion runbook](../planning/runbooks/ingestion-runbook.md) and
stack-specific README files for variables and deployment order. Run commands from
the repository root. All 13 stacks use the [shared S3 backend](tfstate-bootstrap/README.md).
Use `scripts/aws/get-terraform-inputs.ps1 -Stack <name>` for deployed inputs on a
new machine. Real variables, caches and plans remain private local artifacts;
reviewed recovery state snapshots are in `handoff/terraform-state/`. The standalone
scoring and orchestration stacks were retired on 1 November; their shared inputs
are disabled and their states contain no managed resources. Other retained
demonstration resources remain; the Kinesis stream is parked and optional
schedules remain disabled. See [cleanup record](../planning/runbooks/aws-cleanup-20261101.md).

For the existing ingestion stack, `scripts/aws/update-ingestion.ps1` checks a prepared
artifact directory and previews the allowlisted update; `-Execute` applies the saved
plan. `scripts/aws/deploy-ingestion.ps1` is the initial-deployment helper. Neither helper
starts a pipeline run. The updater refuses destructive replacements.
