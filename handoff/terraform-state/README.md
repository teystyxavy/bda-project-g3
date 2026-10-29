# Terraform state handoff — 29 October 2026

This branch contains **all non-empty infrastructure state snapshots found on this
machine**, not a complete or live-verified state set for the deployed pipeline.
It starts from remote `main` commit `2b99def`, including the foundation stack's
S3 backend configuration and the `infra/tfstate-bootstrap` stack.

## Included files

| Stack | Snapshot | Status |
|---|---|---|
| EventBridge trigger | `eventbridge-trigger/terraform.tfstate` | Last available local state, serial 9, last modified 7 October Singapore time; eight resource groups |
| Ingestion | `ingestion/backups/terraform.tfstate.1791382632.backup` | Historical backup, serial 79; not established as current |
| Ingestion | `ingestion/backups/terraform.tfstate.1791328451.backup` | Older historical backup, serial 62 |

`manifest.json` records source timestamps, state lineage, serials and SHA-256
checksums. Copies preserve the original bytes. Credential-pattern, sensitive-field
and embedded-JSON checks found no credentials or secret values in these three
snapshots. They contain infrastructure identifiers and configuration. Future
state snapshots need a separate review; the existing global state ignore rules
remain in place.

## Still needed

No current local resource state was found for foundation (`infra/terraform`),
hosting, scoring, SageMaker, ingestion, cloud ingestion, orchestration, streaming,
dashboard status, dashboard control, or tfstate bootstrap. Get those states from
their current operators or the shared backend where already migrated.
The existence and contents of the AWS backend were not checked during this handoff.
The state-like files in `.terraform/` and `runtime/` are backend initialization
metadata, not the missing resource state, and are deliberately excluded.

Do not restore an ingestion backup as authoritative state or apply against it
without reconciling it with the latest state and deployed resources. A higher
backup serial only identifies the newer of these two backups.

## Use the branch

From an existing clone:

```powershell
git fetch origin
git switch --track origin/handoff/terraform-state-20261029
```

For the EventBridge stack only, copy its snapshot to private local storage before
initializing. Run from the repository root, with Terraform installed. These
commands refuse to overwrite an existing state file and do not deploy resources:

```powershell
$handoffRoot = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-eventbridge-trigger'
$handoffState = Join-Path $handoffRoot 'state/terraform.tfstate'
if (Test-Path -LiteralPath $handoffState) { throw 'Existing state found; reconcile it before restoring this snapshot.' }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $handoffState) | Out-Null
Copy-Item -LiteralPath 'handoff/terraform-state/eventbridge-trigger/terraform.tfstate' -Destination $handoffState
$env:TF_DATA_DIR = Join-Path $handoffRoot 'runtime'
terraform -chdir=infra/eventbridge-trigger init -input=false -reconfigure "-backend-config=path=$($handoffState.Replace('\', '/'))"
Remove-Item Env:TF_DATA_DIR
```

Coordinate a single operator per local-state stack. This branch is a snapshot;
it does not synchronize subsequent Terraform operations. For foundation, obtain
the current shared-backend key and access from its operator and follow
[`infra/tfstate-bootstrap/README.md`](../../infra/tfstate-bootstrap/README.md).
Other stacks still declare local backends; do not assume they have been migrated.
No AWS deployment, state migration, refresh, apply or cleanup was performed for
this handoff.
