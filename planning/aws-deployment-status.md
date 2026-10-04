# AWS foundation deployment — 2026-10-05

The user approved infrastructure deployment. Terraform applied the reviewed
USD 5 monthly-budget plan: **24 resources added, zero changed, zero destroyed**.
A subsequent authenticated Terraform plan returned **no changes**.

| Component | Deployed name |
|---|---|
| Region | ap-southeast-1 (Singapore) |
| S3 bucket | is459-g3-588738581042-ap-southeast-1 |
| Glue job | is459-g3-curate |
| Catalog database/table | is459_g3_curated.smoke_transactions |
| Athena workgroup | is459-g3-analysis |
| Monthly account budget | is459-g3-account-monthly |
| Failure topic | is459-g3-failures |

Direct AWS checks verified all four S3 public-access blocks enabled and AES256
default encryption. The bucket contains only `scripts/curate_transactions.py`
(7,552 bytes). Glue reports zero job runs. The account budget reports USD 5 per
month; its configured notices are 50%, 80% and 100%, not an automatic spending cap.

The SNS email subscription is **pending confirmation** as of deployment.
The recipient must confirm the AWS subscription email before failure emails can
be delivered. The deployed failure route has not been exercised with a job failure.

No dataset has been uploaded and no Glue or Athena workload was executed.
Cloud transformation parity and the first sample run remain pending. The next
step, after approval to execute the sample, is to upload the three smoke inputs,
run the job once and compare Athena totals with `output/aws-smoke/expected.json`.

Terraform state remains outside the repository at
`%LOCALAPPDATA%\IS459-G3\terraform.tfstate`. Preserve it for changes and cleanup.
The reviewed plan has been applied; create a fresh plan for future changes.
No credentials, Terraform state or private variables are committed.
