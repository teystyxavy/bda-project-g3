# Shared Terraform state backend

**1 November cleanup:** `scoring` and `orchestration` are retired. Their S3 state
keys remain, with no managed resources; shared inputs disable their provisioning
flags. Use those disabled inputs when reviewing the retired stacks. Historical
snapshots and older enabled settings must not recreate them. See
[cleanup record](../../planning/runbooks/aws-cleanup-20261101.md).

All 13 stacks, including the coursework archive, use the existing private S3 bucket
`is459-g3-tfstate-588738581042`, with one `<stack>/terraform.tfstate` key per
stack. `foundation` maps to `infra/terraform`. The DynamoDB table
`is459-g3-tfstate-lock` provides locking compatible with the team's Terraform
versions. Both resources live in `ap-southeast-1`, account `588738581042`.

On 31 October 2026, application states were refreshed against live AWS and
published through Terraform's backend with locking, without force-overwriting
state. Resource attributes and outputs were read back and compared. The missing
bootstrap state was recovered by importing its six existing resources; none
were recreated. See the [handoff and verification](../../handoff/terraform-state/README.md).

## Use from another machine

Install AWS CLI v2, Terraform and PowerShell, clone the repository, and update
`main`. Authenticate with the project's AWS profile using
[the login guide](../../planning/runbooks/aws-login.md). Run from the repository root:

```powershell
# Connect to existing state; refuses a missing key by default.
.\scripts\aws\init-terraform.ps1 -Stack ingestion
terraform -chdir=infra/ingestion state list

# Fetch deployed inputs and artifacts into private local storage.
.\scripts\aws\get-terraform-inputs.ps1 -Stack ingestion
$inputs = Join-Path $env:LOCALAPPDATA 'IS459-G3/shared-inputs/ingestion/terraform.tfvars.json'
terraform -chdir=infra/ingestion plan "-var-file=$inputs"
```

Other stack names: `hosting`, `scoring`, `sagemaker`, `cloud-ingestion`,
`orchestration`, `streaming`, `dashboard-status`, `dashboard-control`,
`eventbridge-trigger`, `foundation`, `tfstate-bootstrap`, and `archive`.
The module directory for `foundation` is `infra/terraform`.
Pass `-Profile your-tools-profile` if the project profile has a different name.

The input helper checks the backend lineage/serial against the published input
inventory. It downloads ingestion artifacts from their immutable S3 deployment
prefix and deployed dashboard Lambda packages through AWS. It verifies SHA-256
hashes and replaces local paths for the current machine. Files stay under
`%LOCALAPPDATA%/IS459-G3/shared-inputs`. If state has since changed, refresh the
handoff inputs rather than using stale variables. Never plan existing optional
stacks with their disabled defaults: that would propose deleting retained resources.

Downloaded artifact contents match the deployed hashes. A normal plan on another
machine can still show changes to local `source`/`filename` paths, plus dependent
JSON formatting, because Terraform records those paths in state. Review these
separately from content or infrastructure changes. The helpers never apply them.

Both helpers initialize the backend and set `TF_DATA_DIR` for the current
PowerShell process. Reinitialize in each new terminal or after changing stacks.
They do not apply infrastructure changes or grant permissions. The teammate
must already have access to the bucket, lock table and relevant AWS resources.

Shared non-secret settings are in [`infra/shared.s3.tfbackend`](../shared.s3.tfbackend).
Manual initialization is also supported:

```powershell
$env:AWS_PROFILE = 'is459-g3-tools'
terraform -chdir=infra/ingestion init -reconfigure -backend-config=../shared.s3.tfbackend -backend-config=key=ingestion/terraform.tfstate
```

## State and recovery

S3 is authoritative. Git and the versioned S3 handoff archive contain recovery
snapshots. Previous local states are obsolete after this cutover. Newly populated
keys have backend-generated lineage/serial histories; use current S3 metadata
rather than pushing an old local lineage over them. Foundation's existing shared
history was preserved.

Bootstrap now also uses S3, under `tfstate-bootstrap/terraform.tfstate`. This is
possible because its bucket and table already exist. Both have Terraform
`prevent_destroy` protection. Do not destroy the backend while any project state
depends on it. Before eventual approved cleanup, export all states and move
bootstrap management to an independent local backend. For a new AWS account,
create the backend using a separate local bootstrap first; this checkout targets
the existing Group 3 backend.

The bucket has versioning, AES256 encryption and all four public-access blocks
enabled. Do not expire state histories. DynamoDB locking is retained for team
compatibility; newer Terraform versions warn it is deprecated in favor of native
S3 locking. Coordinate a team upgrade before changing the lock mechanism.
