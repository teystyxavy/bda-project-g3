# Terraform state snapshots — 31 October 2026

This folder contains all 25 non-empty infrastructure state files found in this
machine's project state directory (`%LOCALAPPDATA%/IS459-G3`) and repository
`infra/` directory. It includes the three snapshots previously published on
`handoff/terraform-state-20261029`, plus the other available states and backups.
These are local snapshots, not a live-verified record of AWS resources.

| Stack | Latest available local serial |
|---|---:|
| Foundation (`infra/terraform`) | 32 |
| Hosting | 12 |
| Scoring | 25 |
| Orchestration | 4 |
| Streaming | 31 |
| Cloud ingestion | 11 |
| Dashboard control | 19 |
| Dashboard status | 11 |
| EventBridge trigger | 9 |
| Ingestion | 132 |
| SageMaker | 4 |

Each stack's `terraform.tfstate` is its latest available local snapshot. Older
files are under `backups/`; some ingestion backups have identical contents under
different original filenames. `manifest.json` records SHA-256 checksums, lineage,
serial, Terraform version and source modification time. Copies preserve original
bytes. No bootstrap resource state was found for `infra/tfstate-bootstrap`.

Credential-pattern and secret-field checks, including embedded JSON, found no
credentials or secret values in the included files. Foundation's Terraform
sensitive markers refer to budget notification settings and an SNS email endpoint;
these files retain notification email addresses and infrastructure identifiers.
Future snapshots require their own review. Global Terraform state ignore rules
remain enabled; only these explicitly reviewed snapshots are committed.

## Using these snapshots

Fetch and update `main` to obtain the snapshots. Before restoring one, confirm
the authoritative backend with the stack's operator and compare its lineage and
serial with any newer state. Copy a selected snapshot into private local storage
and initialize the appropriate backend there. Do not overwrite an existing state
or treat a backup as current without reconciliation. Coordinate a single operator
for each stack using a local backend.

Foundation declares an S3 backend. Its local snapshot may predate migration;
obtain the current bucket/key and compare with remote state before using it.
See [backend documentation](../../infra/tfstate-bootstrap/README.md). Files inside
`.terraform/` and `runtime/` are backend initialization metadata and are excluded.
Generated test fixtures are also excluded.

This handoff did not run AWS deployment, state migration, refresh, apply or cleanup.
Snapshots in Git do not synchronize subsequent Terraform operations.
