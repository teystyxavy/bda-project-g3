# Shared Terraform state backend

Creates the S3 bucket + DynamoDB lock table that every other `infra/` stack's
`backend "s3" {}` points at, instead of each stack's state living only on one
team member's machine under `%LOCALAPPDATA%\IS459-G3\`.

This stack's own state stays on the **local backend, permanently** — it
cannot store its state in the bucket it creates (chicken-and-egg). Apply it
from one machine, once, and keep that local state file safe; it is the only
record of how to manage the backend bucket/table themselves going forward.

## Deploy (once)

```powershell
terraform -chdir=infra/tfstate-bootstrap init
terraform -chdir=infra/tfstate-bootstrap plan -var aws_account_id=588738581042 -out=bootstrap.tfplan
# Review the plan before applying -- this is a normal AWS-creating deployment
# and needs the same explicit approval as any other stack here.
terraform -chdir=infra/tfstate-bootstrap apply bootstrap.tfplan
terraform -chdir=infra/tfstate-bootstrap output
```

## Migrate an existing stack onto it

Run from the machine that currently holds that stack's authoritative state
(see `planning/aws-deployment-status.md` / the ingestion runbook for which
machine that is per stack today):

```powershell
terraform -chdir=infra/ingestion init -migrate-state `
  -backend-config="bucket=<bucket_name output>" `
  -backend-config="key=ingestion/terraform.tfstate" `
  -backend-config="region=<aws_region output>" `
  -backend-config="dynamodb_table=<dynamodb_table_name output>"
```

Use a distinct `key` per stack (`foundation/terraform.tfstate`,
`scoring/terraform.tfstate`, `sagemaker/terraform.tfstate`, ...); `bucket`,
`region` and `dynamodb_table` stay the same across all of them. After a
successful migration, that stack's `versions.tf` should read
`backend "s3" {}` (empty, same as the local-backend convention — values are
still supplied at `init` time, not hardcoded).

## Why DynamoDB locking, not native S3 locking

Terraform's native S3 state locking (`use_lockfile = true`, no DynamoDB table
needed) requires Terraform ≥1.11. State files already on this project's
machines show both 1.9.8 and 1.16.4 in active use, so DynamoDB locking is
used instead — it works on every version the team currently has installed.
