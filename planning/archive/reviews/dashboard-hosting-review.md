# Dashboard hosting preparation — 2026-10-05

Latest result (2026-10-06): the user approved staging, four uploads and public
delivery. Staging applied **9 additions, 0 updates, 0 deletions**, then one
enabled-only distribution update published the verified release. Exact asset
hashes, security/origin restrictions and live browser checks passed; the
post-apply plan shows no changes. The private origin serves a public static demo:
[d1n61tlw9pcrog.cloudfront.net](https://d1n61tlw9pcrog.cloudfront.net/).
See [publication result](../../evidence/dashboard-publication-result-20261006.json).
See [current deployment/publication review](dashboard-publication-review-20261006.md)
and [machine-readable preparation](../../evidence/dashboard-hosting-preparation-20261006.json).
The sections below retain the initial hosting design and historical preparation.

Status: local preparation only. No AWS resource changes, uploads, publication,
model training or paid jobs were performed in this step. The already-running
local preview is unchanged. The separate AWS scoring job subsequently passed its
1,024-row smoke; a full-data plan and cloud-score dashboard integration are now
prepared. See [full scoring review](full-cloud-scoring-review.md). Hosting remains
undeployed. A separately approved full-data scoring run has since succeeded;
the verified cloud-backed dashboard and four-file release now exist locally.
Latest release: `output/cloud-dashboard-release-20261006/site/`, **620,811 bytes**,
with hashes in its parent `manifest.json`. Its source is
`output/cloud-model-dashboard-20261006/`; model explanations remain local.
See [full result metadata](../../evidence/full-cloud-scoring-result-20261006.json). No publication occurred.
The release details below retain the earlier local-model preparation snapshot.

## Prepared release

```powershell
output/ml-venv/Scripts/python.exe scripts/dashboard/prepare-dashboard-release.py --source output/model-dashboard-20261005 --output output/dashboard-release-new
```

Use a fresh directory. The saved release is `output/dashboard-release-20261005/`.
Only `site/index.html`, `site/dashboard.css`, `site/dashboard.js` and
`site/dashboard.json` may be uploaded to the hosting bucket's `dashboard/` prefix.
The manifest stays outside the served directory and records hashes, content types,
sizes and the original snapshot hash. Never sync the repository, an entire output
directory, the manifest, Terraform state, Parquet, model artifacts or credentials.

The release totals **620,190 bytes**. Packaging removes the curated local path,
checks for credential-like fields/values and rejects row-level evaluation labels
in queues. Aggregate evaluation metrics remain visible by design. Synthetic bank
and account identifiers remain in the review queue: publishing them still needs
human approval. These checks are defensive safeguards, not a comprehensive secret
audit. Rule results, BQ2 aggregates and frozen-model provenance are preserved.

## Separate Terraform stack

`infra/hosting/` does not read or modify the batch stack or its bucket. It uses
the same locked AWS provider version, but must have **its own local state outside
OneDrive**, for example `%LOCALAPPDATA%/IS459-G3/hosting.tfstate`. Never point it
at the batch stack's state. Terraform >=1.9 is required for cross-variable gates.

- Default `provision_hosting=false`: zero resources.
- Approved staging with `provision_hosting=true`: nine resources, including a
  dedicated private, versioned, AES256-encrypted bucket and a disabled distribution.
- Public delivery additionally requires `publish_dashboard=true` and
  `public_demo_approved=true`. These variables record approval, not replace it.
- No upload resources, paid jobs, schedules, DNS purchase, certificate purchase,
  WAF subscription or CloudFront flat-rate plan enrollment are included.

The complete bucket policy is in `infra/hosting/main.tf` and exposed as the
`dashboard_bucket_policy` output for review. Its first statement allows only
`s3:GetObject` on `dashboard/*` to `cloudfront.amazonaws.com`, restricted by
`AWS:SourceArn` to this distribution. Its second statement denies insecure
transport on the bucket and objects. It grants no anonymous S3 read, writes,
bucket listing or access to the pipeline bucket. All four S3 public-access blocks
stay enabled; ACLs are disabled. OAC always signs REST-origin requests, not S3
website requests. Viewers are redirected to HTTPS; GET/HEAD only. Security headers
prevent framing and MIME sniffing. The default CloudFront certificate is used;
this configuration does not claim a custom-certificate TLS minimum.

These controls follow AWS's [OAC guidance](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
and [S3 security guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html).
Crucially, OAC protects the origin, **not viewer access**. Once enabled, this
configuration serves the synthetic dashboard publicly without a login. If a
private dashboard is desired, design viewer authentication before deployment.

`PriceClass_100` deliberately favors lower-cost delivery locations and may add
latency for Singapore viewers; it is not a traffic or spending cap.

## Cost and operations gate

Local preparation adds no AWS usage. Later staging incurs storage/request usage
when files are uploaded; public delivery incurs usage-dependent CDN requests and
transfer. Versioned objects also continue occupying storage after replacement.
No current account balance or forecast was refreshed in this step. The existing
USD 10 monthly budget is an alert target, **not a hard limit**.

Before approval, refresh account costs/forecast, available shared allowances,
Singapore S3 Standard storage/PUT/GET rates and CloudFront delivery rates at the
[S3 pricing page](https://aws.amazon.com/s3/pricing/) and
[CloudFront pay-as-you-go page](https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/).
Do not confuse this Terraform configuration with the separately advertised
CloudFront flat-rate Free plan: it does not enroll in that plan. No assumption
is made that free allowances remain unused by the rest of the account.

Compute a reproducible estimate from the manifest: stored bytes including retained
versions × GB-month storage rate; upload counts × PUT rate; cache misses × GET
rate; measured viewer bytes × delivery rate; viewer request counts × CDN request
rate. Include other account services and any chosen logging. Use a calculator or
script, not mental arithmetic. Public traffic is unbounded, so an under-USD-10
guarantee is not possible with this pay-as-you-go plan.

Recommend S3 access logging, CloudTrail data events and CloudWatch request metrics
for production observability, with encrypted destinations (SSE-S3 for access logs,
KMS for CloudTrail/CloudWatch). These optional paid controls are not provisioned
in this minimal staging stack; review their costs before adding them. Basic
service metrics and existing budget alerts do not replace request auditing.

## Verification and next actions

Native Python suite: 30 passed, one native Spark module skipped. Release tests
cover allowlisting, unchanged source/metrics/identifiers, fresh output protection,
private paths and queue-label/credential rejection. Terraform validation and all
four mocked hosting tests passed. Mock tests do not
prove actual IAM permissions or cloud delivery.

Next safe implementation step: [cloud scoring runtime parity](cloud-scoring-preparation.md).
For hosting, obtain approval for paid staging and the public synthetic-data demo,
then initialize a **separate private state**, generate/review a fresh authenticated
plan, stage the four manifest-verified files with their declared content types,
and review another plan before enabling delivery. Verify HTTPS, direct S3 denial,
unapproved origin paths denied, both business views, leading-zero IDs and metrics.
Do not run these deployment steps without explicit user approval.

To stop delivery, use a reviewed plan with `publish_dashboard=false`; do not set
`provision_hosting=false` on an existing stack casually, because that plans resource
destruction. Removing bucket versions or destroying resources requires separate
cleanup approval; `force_destroy=false` prevents automatic bucket emptying.
