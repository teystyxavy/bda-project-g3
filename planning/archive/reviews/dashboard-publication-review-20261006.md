# Verified dashboard deployment and publication review — 2026-10-06

Latest status: the user explicitly approved the nine-resource deployment,
four uploads and public HTTPS delivery. The exact saved staging plan and release
hashes passed their guards. Staging applied **9 additions, 0 updates,
0 deletions**. Live bucket and disabled-distribution guards passed; all four
uploads passed versioned readback hashes and metadata checks. The fresh enable
plan changes only `enabled=false` to `true` for distribution `EU1WB6XM4GKHQ`,
with zero additions/deletions. Its approved apply completed, and the post-apply
plan shows no changes. Public HTTP/security and browser checks **passed**.
Live dashboard: [https://d1n61tlw9pcrog.cloudfront.net/](https://d1n61tlw9pcrog.cloudfront.net/).
See [publication result](../../evidence/dashboard-publication-result-20261006.json).
Sections below retain the reviewed scope and pre-deployment evidence.

This review concerns the
verified full-population dashboard from the successful frozen scoring run.
No new paid batch job, model training, IAM identity permission change or cleanup
is proposed. Approval requested below covers hosting and public delivery only.

## Exact proposed scope

1. Apply the saved staging plan: **9 additions, 0 updates, 0 deletions** to a
   separate hosting stack. It creates a private encrypted/versioned S3 bucket,
   four bucket controls, the bucket policy, CloudFront OAC, security headers
   policy and a **disabled** CloudFront distribution. No object uploads are in
   Terraform. The batch/scoring resources remain outside this state.
2. Upload only the four manifest-verified runtime files (**620,811 bytes**) to
   `s3://is459-g3-dashboard-588738581042-ap-southeast-1/dashboard/`, preserving
   declared content types, AES256 and cache controls. Read back each exact key
   to verify hashes and metadata; keep receipts outside OneDrive.
3. Generate a fresh live-state enable plan. Require **only one distribution
   update**, `enabled=false` to `true`, with no additions/deletions and the
   reviewed controls unchanged. Within explicit publication approval, apply
   that plan and verify public HTTPS delivery and both business views.

Private saved plan:
`C:/Users/lowth/AppData/Local/IS459-G3/hosting-staging-reviewed-20261006.tfplan`.
SHA256: `b7b22689ccc1f8e4aa8314f5132a27bcc4a8ef4f4ec6e5d9dfd5614faabcc6d4`.
Separate private backend: `C:/Users/lowth/AppData/Local/IS459-G3/hosting.tfstate`.
Provider: locked `hashicorp/aws` 6.67.0; region `ap-southeast-1`; allowed account
`588738581042`. No staging plan was applied and no public approval flag was set.

The saved plan cannot predict the future distribution ID/domain or its final
policy ARN before creation. Its OAC policy references only that distribution.
The enable plan must therefore be checked again after staging. Unexpected
resource changes or permission expansion are outside this approval scope.

## Public contents and origin protection

Public release: `output/cloud-dashboard-release-20261006/site/`.
Manifest: its parent `manifest.json`, retained outside the web root. Only
`index.html`, `dashboard.css`, `dashboard.js`, `dashboard.json` are uploaded.
The manifest, Parquet, model/runtime artifacts, CSV exports, state, credentials
and private verification downloads are excluded. All four file hashes/sizes
were rechecked against the manifest in this review.

Anyone with the CloudFront URL can read the synthetic review queues, transaction
and bank/account identifiers (including leading zeros), scores, locally
reconstructed explanations, aggregate evaluation metrics and treasury views.
There is **no viewer login**. Queue-level laundering labels are excluded.
Results describe this synthetic dataset; scores are not calibrated probabilities.

The origin uses all S3 public-access blocks, disabled ACLs, AES256, versioning
and a TLS deny. Signed OAC permits only `s3:GetObject` on `dashboard/*` from the
specific distribution. Viewers use HTTPS, GET/HEAD and security headers. OAC
protects the S3 origin; it does not authenticate viewers. These controls follow
[AWS OAC documentation](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html)
and [S3 security guidance](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security-best-practices.html).

## Verification completed

- Authenticated Terraform staging plan: nine creates only; distribution disabled.
- Terraform formatting/validation and **4 mocked hosting tests passed**.
- Read-only simulation allowed all **15** checked tools-principal create,
  configure, tag, upload/read and distribution-update actions. Simulation is
  supporting evidence; real service authorization is verified during staging.
- Exact proposed bucket HeadBucket returned 404. CloudFront inventory CLI
  returned empty output; no distribution count or name-collision conclusion
  is inferred. Provider creation will report any conflicting names.
- Full scoring acceptance remains passed for **5,078,345** rows; dashboard
  model metrics/order/explanation text match the frozen native snapshot.
  Original rules and treasury structures remain unchanged.
- Release hashes, sizes, allowlist and prepared-not-published status passed.
- The exact packaged release was previewed locally at `http://127.0.0.1:4177/`;
  AML metrics and the full-population treasury view display correctly, with no
  console errors. The approval screenshot is
  `output/cloud-dashboard-release-20261006/publication-preview.png`, outside
  `site/` and excluded from upload.

Private plan/JSON/text, permission simulation and pricing/budget responses are
outside OneDrive. Summary hashes and reproducible calculation inputs are in
[dashboard-hosting-preparation-20261006.json](../../evidence/dashboard-hosting-preparation-20261006.json).

## Incremental cost estimate

Current Singapore S3 Price List results: Standard storage **USD 0.025/GB-month**,
PUT/COPY/POST/LIST **USD 0.005/1,000**, GET **USD 0.004/10,000**.
See [S3 pricing](https://aws.amazon.com/s3/pricing/).
The configured `PriceClass_100` uses North America/Europe delivery locations,
which may increase latency for Singapore viewers. Conservative Europe HTTPS
rate **USD 0.012/10,000** and first paid transfer tier **USD 0.085/GB** are
verified on [CloudFront pay-as-you-go pricing](https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/).
This stack does not enroll in a flat-rate subscription.

Deterministic calculation: four assets, one stored version for one month,
every full load downloads all 620,811 bytes, four HTTPS requests per load,
every origin request misses the cache, no compression/browser caching and
**no free allowance or credits deducted**. AWS's account-plan lookup returned
ResourceNotFoundException and free-tier usage returned no entries; this does
not establish available allowances.

| Full dashboard loads | Incremental estimate (USD) |
| ---: | ---: |
| 100 | 0.005589 |
| 1,000 | 0.055579 |
| 10,000 | 0.555483 |

These scenarios include one month of release storage, four PUTs, worst-case
origin GETs and viewer delivery/HTTPS requests. They exclude control-plane and
verification requests, retained future versions, extra/malicious traffic,
logging, taxes and existing pipeline services. They are examples, not a
guarantee or hard spending cap. Public traffic is unbounded.

Refreshed AWS Budget response: **USD 0.936 actual**, **USD 5.87 forecast**,
**USD 10 monthly alert**, last updated **2026-10-06 00:25:29.981 SGT**.
This delayed account-wide response does not include a reconciled invoice for
the latest run. The course budget remains USD 250; the USD 10 alert is not a
project lifetime limit or enforcement cap. No budget change is proposed.

## Acceptance after approval

Before applying, verify saved-plan SHA, source hashes and allowed account;
retain separate state. After staging, verify bucket encryption/versioning,
ownership/public blocks, disabled distribution and exact OAC policy. Upload
only the four specified keys, with `no-cache` for HTML and
`public,max-age=300` for the other three files, and compare readback SHA256.
Record version IDs and content types. Check the live enable plan before applying.

After CloudFront deploys, verify HTTPS root and all four assets, direct
anonymous S3 denial and absent/unapproved origin paths denied. Browser-check
rule/model switching, validation/test/tail selection, transaction evidence,
treasury all/daily selection, leading-zero identifiers, source provenance and
no console errors. Save the public URL, screenshot and evidence. Publication
does not rerun curation/scoring or change the frozen model.

If staging or upload fails, preserve evidence; do not enable delivery from an
unverified release. Stopping delivery requires a reviewed distribution-disable
plan; deleting resources/versions still requires separate cleanup approval.

## Approval requested

The user granted this approval and the scope completed. This section retains
the original approval request; cleanup remains separately gated.

Approve the **nine-resource separate hosting deployment, four exact-file
uploads, subsequent single distribution-enable update and public HTTPS
synthetic-data demo**, accepting usage-dependent traffic costs.
The workspace [AGENTS.md](../../../../AGENTS.md) states:
“Do not run deployments, create paid cloud resources, or perform destructive
AWS cleanup unless the user explicitly requests it.” The previously approved
full scoring scope ended at local release export; publication was excluded.

## Completed publication acceptance

Distribution `EU1WB6XM4GKHQ` is **Deployed** and enabled. The separate stack
contains nine resources; staging added nine and public enable changed one.
Its private state is retained outside OneDrive. Live public-access blocks,
AES256/versioning, disabled ACLs and exact OAC/TLS policy passed readback.
Exactly four keys are stored, totaling 620,811 bytes. Every versioned object
matches the manifest hash, content type, cache control and encryption metadata.

The public root and all four assets returned HTTP 200 with exact release bytes.
Security headers passed; HTTP redirects to HTTPS (301). Anonymous direct S3
access to the existing index returned 403. The unpublished manifest and test
path also returned 403. Nine HTTP acceptance checks passed.

The live browser passed rule/model switching, validation/test/tail selection,
transaction evidence with leading-zero identifiers, treasury all-days/daily
selection and cloud-score provenance checks. It reported no console errors.
Live AML/treasury screenshots are in the local release parent directory and
were not uploaded. The browser tab is left open on the public dashboard.

The original manifest and preparation JSON remain immutable historical
packaging/pre-approval records; the [publication result](../../evidence/dashboard-publication-result-20261006.json)
records the actual published state. No new paid curation/scoring run, retraining,
identity permission update, batch-stack change or cleanup occurred.
