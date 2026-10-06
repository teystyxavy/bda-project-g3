# AWS batch plan review — 2026-10-05

An authenticated Terraform plan has been generated using `is459-g3-tools` in
Singapore (`ap-southeast-1`). The expected account check passed. The saved plan
is `%LOCALAPPDATA%\IS459-G3\batch.tfplan`; account/email variables and backend
state are kept in the same private directory outside this OneDrive checkout.

Reviewed result: **24 additions, zero modifications, zero deletions.**
Applied after user approval on 2026-10-05; the follow-up plan found no changes.
See [deployment status](../../aws-deployment-status.md) for verification and remaining steps.

| Planned component | Terraform resource count |
|---|---:|
| Private S3 bucket, access/encryption/versioning/lifecycle policies, Glue script upload | 7 |
| Glue job and catalog database/table | 3 |
| Athena workgroup and two saved queries | 3 |
| Two IAM service roles and their scoped policies | 4 |
| Two log groups, failure event rule/target, SNS topic/subscription | 6 |
| Account-wide monthly budget | 1 |

The Glue job is limited to two G.1X workers, ten minutes, no retries and one
concurrent execution. Athena has a 1 GiB per-query scan cutoff. The USD 5
monthly budget sends notices at 50%, 80% and 100%; it is not a spending stop
and does not enforce the USD 250 cumulative course allowance.

Apply creates the foundation and uploads the job script. It does not upload
transaction data, start a Glue job, publish dashboards or schedule processing.
Confirm the SNS subscription email after an approved apply. Data upload and
the 1,000-row smoke execution are a separate next step.

The plan verifies configuration and authenticated planning. It does not prove
that the login has every create/PassRole permission or that service quotas,
resource names and Lake Formation permissions permit deployment. Any apply
failure must be assessed against saved state before retrying.

Saved-plan SHA-256:
`42706BBF2F7211AEE6F8E2FDF5B44A73768D62A613990006FA76E283B885CAD4`.
This review applies to that plan. Regenerate/review if infrastructure code or
inputs change. Deployment requires explicit approval under workspace AGENTS.md
because it creates AWS resources and can incur charges.
