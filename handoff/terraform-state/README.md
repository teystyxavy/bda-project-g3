# Terraform state snapshots — 31 October 2026

This folder contains 26 infrastructure state snapshots: all 25 non-empty files
found in this machine's project state directory (`%LOCALAPPDATA%/IS459-G3`) and
repository `infra/` directory, plus the shared S3 foundation state. It includes the three snapshots previously published on
`handoff/terraform-state-20261029`, plus the other available states and backups.
All 11 latest stack snapshots were checked against live AWS on 31 October 2026.
Some snapshots differ from live resources; see the verification results below.

| Stack | Latest available local serial |
|---|---:|
| Foundation (`infra/terraform`, shared S3 backend) | 26 |
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

Each stack's `terraform.tfstate` is its latest available snapshot (shared S3 state
for foundation, local state for the other stacks). Older
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

Foundation uses `s3://is459-g3-tfstate-588738581042/foundation/terraform.tfstate`.
Its serial 26 snapshot matches live provider reads. The former local serial 32
snapshot has a different lineage and substantive drift; it is preserved as
`foundation/backups/pre-live-verification-local-serial32.tfstate`. Serial numbers
from different lineages cannot be used to rank state freshness.
See [backend documentation](../../infra/tfstate-bootstrap/README.md). Files inside
`.terraform/` and `runtime/` are backend initialization metadata and are excluded.
Generated test fixtures are also excluded.

## Live verification results

The [machine-readable report](live-verification-20261031.json) records refresh-only
plans against account `588738581042` in `ap-southeast-1`. Terraform 1.16.4 with AWS
provider 6.67.0 read all 141 resource instances across the 11 latest snapshots.
Checks used isolated local copies and original provider default tags. No working
backend or AWS resource was changed. Provider reads check recorded resources;
they do not prove completeness of the inventory or correct application behavior.

| Result | Stacks |
|---|---|
| Exact match on provider reads | Foundation (shared backend), hosting |
| Empty-value normalization and/or IAM policy readback only | Cloud ingestion, EventBridge trigger, ingestion, orchestration, SageMaker, scoring |
| Current API Gateway deployment ID missing from snapshot | Dashboard control, dashboard status |
| Stale resource entries | Streaming |

Every IAM role inline-policy readback difference matches an identical separately
tracked `aws_iam_role_policy` resource. These duplicate representations and
null-versus-empty values are reported without claiming a byte-for-byte match.

Streaming still records `aws_kinesis_stream.replay[0]` and
`aws_lambda_event_source_mapping.replay[0]`, which provider reads found absent.
This is consistent with the documented parked stream, but the snapshot remains
stale. Do not restore it as current without reconciling those entries.

The old local foundation snapshot also records absent Athena named queries and
an SNS subscription, plus changed budget settings, Glue table schema and script
object metadata. The replacement shared-backend snapshot has none of that drift.

The backend bucket and DynamoDB lock table exist. The bucket has versioning,
AES256 encryption and all four public-access blocks enabled; the lock table is
ACTIVE. Its only state object is `foundation/terraform.tfstate`. No bootstrap
state was found. Historical backups were preserved without asserting they match
live AWS. `live_state_checked` in the manifest records completion of these checks;
`live_state_verified` remains false because the full snapshot set is not drift-free.

This handoff did not run AWS deployment, state migration, refresh, apply or cleanup.
Snapshots in Git do not synchronize subsequent Terraform operations.
