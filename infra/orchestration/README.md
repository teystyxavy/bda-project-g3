# Reviewed batch workflow

**Retired 1 November 2026 during the user-authorized AWS cleanup.** The earlier
`is459-g3-reviewed-batch` workflow and its dedicated role/policy were removed
through Terraform. The current `is459-g3-ingest` workflow provides orchestration.
Shared inputs now set `provision_orchestration=false`; the shared state has no
managed resources. The instructions below are historical and do not describe a
currently deployed demonstration. See [cleanup](../../planning/runbooks/aws-cleanup-20261101.md).

Standard Step Functions coordinates frozen Glue scoring and one Athena treasury
query over the previously verified curated data. It does not ingest, curate,
train, export explanations or publish a dashboard. See
[`planning/archive/reviews/orchestration-review-20261006.md`](../../planning/archive/reviews/orchestration-review-20261006.md).

The stack is opt-in. Default flags create nothing for a new stack; **do not plan
with those defaults against the deployed stack**, since that would propose
destruction. Current deployed inputs are account `588738581042`, region
`ap-southeast-1`, `provision_orchestration=true`, and
`run_id=full-orchestrated-20261006`. Scheduling flags remain false.

State is in the [shared S3 backend](../tfstate-bootstrap/README.md). Initialize
from the repository root:

```powershell
.\scripts\aws\init-terraform.ps1 -Stack orchestration
terraform -chdir=infra/orchestration validate
terraform -chdir=infra/orchestration test
terraform -chdir=infra/orchestration plan -input=false -var=aws_account_id=588738581042 -var=provision_orchestration=true
```

The tests use a mock provider and create no AWS resources. The plan above is
read-only. Do not apply a changed plan or start another paid execution without
approval. Scoring uses the separate `scoring/terraform.tfstate` S3 key.

To demonstrate the completed execution without replaying data:

```powershell
aws stepfunctions describe-execution --execution-arn arn:aws:states:ap-southeast-1:588738581042:execution:is459-g3-reviewed-batch:full-orchestrated-20261006
aws stepfunctions get-execution-history --execution-arn arn:aws:states:ap-southeast-1:588738581042:execution:is459-g3-reviewed-batch:full-orchestrated-20261006
```

Show the request/job/source checks, parallel branches and real success-marker
check, then the local reconciliation reports. Execution success means outputs
are ready to verify; the verifier reports establish the business outputs.
Keep any screenshots/video free of credentials and private Terraform state.

For a future approved run, prepare a new safe run ID and fresh output/temp
prefixes, review the corresponding scorer arguments/permissions and state-machine
definition, and verify source/model contracts. Use that run ID as the execution
name. The prepared input defaults to `execution_approved=false`; do not edit it
to authorize an unapproved run. Failed outputs are retained for diagnosis.

Optional one-off EventBridge Scheduler is absent from the deployed stack. Its
four resources require a separate reviewed time and approval. It defaults to
disabled, uses schedule-group-scoped trust, a fixed execution name and zero
delivery retries. Do not introduce daily replay of the historical dataset.
