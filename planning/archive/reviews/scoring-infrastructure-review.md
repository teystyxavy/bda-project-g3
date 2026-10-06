# Frozen scoring infrastructure review — 2026-10-05

Latest status (2026-10-06): the approved fourth smoke **SUCCEEDED** in 120
execution seconds. Two updates applied with zero uploads. Explicit Hadoop
success-marker creation resolved the observed commit failure; S3 `_SUCCESS`
exists and ordinary reconciliation passed all 1,024 rows with zero score
difference, exact features/IDs, partitions and cohort top-100 order. Strict
account/job trust remains verified. Seven infrastructure tests pass and the
post-apply plan showed no changes. See [verified smoke evidence](scoring-success-marker-review.md).
Earlier failed prefixes remain invalid. The separately approved full-data run
subsequently succeeded in 205 execution seconds, and all 5,078,345 rows passed
verification. The cloud-backed dashboard/release were subsequently approved for
public hosting and passed publication acceptance. See [full result metadata](../../evidence/full-cloud-scoring-result-20261006.json)
and [publication result](../../evidence/dashboard-publication-result-20261006.json).

Earlier status: the approved localization retry applied two updates,
uploaded eight verified artifacts and ran once. Its 1,024 scores exactly match
the native fixture, but the job FAILED at output commit (S3 ListBucket denied).
Partial output is forensic evidence only. A narrow EMRFS marker fix and fresh
two-update plan pass local and IAM checks; one third paid smoke awaits approval.
No further uploads are needed. Earlier sections are historical snapshots;
the final section below supersedes their pending-retry status.

## Isolation and limits

Default `provision_scoring=false` creates nothing. Use separate private local
state outside OneDrive, e.g. `C:/Users/lowth/AppData/Local/IS459-G3/scoring.tfstate`;
never use the batch or hosting state. Turning provisioning off on an existing
stack plans destruction and requires separate cleanup approval.

The stack references the existing private pipeline bucket and SNS topic; it
does not manage them. It defines a separate Glue job/role/policy, two 14-day log
groups, and failure-only EventBridge/SNS delivery. No schedule, run trigger,
automatic upload, curation modification, catalog, dashboard or budget resources.
Glue limits: version 5, two G.1X workers, ten minutes, zero retries, one concurrent
run. Path/count/hash/dependency arguments are non-overridable. The default input
is a **not-yet-uploaded** 1,024-row fixture, not the full dataset.

## Permissions and trust

The complete runtime policy is `local.permissions` in `infra/scoring/main.tf`,
also exposed as `runtime_permissions`. Review all its statements before apply:

| Statement | Allowed scope |
| --- | --- |
| BucketLocation | Location lookup on the one pipeline bucket |
| ListOnlyRunPrefixes | Listing only exact input/artifact/output/temp prefixes and descendants |
| ReadInputArtifactsAndOwnOutput | Reads on those prefixes, including output existence checks |
| WriteAndCommitOwnOutput | Writes/deletes/multipart cleanup and part listing only on this run's output/temp paths |
| WriteOnlyScoringLogs | Create streams and append events only in the two scoring log groups |

No curated/raw/artifact writes, bucket-wide listing, version deletion, public
ACL, bucket creation, IAM, catalog, Athena or dashboard permissions. DeleteObject
supports Spark commit/temp cleanup, not source deletion. The separate failure
role only publishes to the existing failure SNS topic and trusts the exact
EventBridge rule/account. No broad AWS-managed policy is attached.

The Glue role's fail-closed trust requires exact job `aws:SourceArn` and matching
`aws:SourceAccount`. **Glue's population of this STS context is not verified by
mock tests or the local container.** Investigate service support before the
approved smoke run; do not call this cloud-certified or automatically remove
conditions on failure. The existing curation role remains untouched.

Consulted [AWS Glue permissions guidance](https://docs.aws.amazon.com/glue/latest/dg/create-service-policy.html)
and checked public authorization mappings for S3 location/list/read/write/delete,
multipart cleanup/part listing, log stream/events and SNS Publish. Variant-only
ACL, Object Lambda, Object Lock, version and tag permissions were not added to
this standard S3 workflow.

IAM Policy Autopilot 0.3.0 was run on the scorer with account, region, service
hints and Terraform directory. It returned `Policies: []`: Spark/Hadoop mediates
the S3 operations instead of direct boto3 calls. This is a coverage limitation,
not proof that no permissions are needed; the runtime policy uses the verified
authorization-reference fallback.

For deployment-user permissions, first render a reviewed authenticated plan to
private JSON, then use deterministic plan analysis rather than a hand-built
admin policy. The following is for that later review, **not executed yet**;
the plan JSON does not yet exist:

```powershell
$env:DISABLE_IAM_POLICY_AUTOPILOT_TELEMETRY = 'true'
output/ml-venv/Scripts/uvx.exe iam-policy-autopilot@latest generate-policies 'C:/Users/lowth/AppData/Local/IS459-G3/scoring-plan.json' --region ap-southeast-1 --account 588738581042 --pretty
```

Do not use `--upload-policies`. Plans/state may contain sensitive values: restrict
access, never print or commit them. Use temporary login credentials and restrict
deployment PassRole to the new scoring/failure roles. Recommend encrypted
CloudTrail auditing and IAM change alarms as a separately cost-reviewed
operations change; none are automatically provisioned here.

## Budget and next gates

AWS verified the existing COST/MONTHLY budget `is459-g3-account-monthly` at USD 10;
its actual-spend 50/80/100 alerts were retained. Terraform defaults, example,
login helper and private `batch.tfvars.json` now use 10, and validation rejects
limits above 10. Historical USD 5 deployment records remain historical.
**This is an alert threshold, not a hard spending cap.** No Budget Actions,
automated shutdown or additional budgets were created. Budget-only UpdateBudget
avoided applying unrelated infrastructure. Current spend/forecast and pricing
were not refreshed for this review.

1. Verify strict Glue trust compatibility; refresh account costs and regional
   Glue/S3/log rates. Include runtime upload/storage and logging, not only compute.
2. Obtain deployment/upload approval, initialize separate private state and
   review an authenticated plan. Mock tests are not an AWS plan or IAM simulation.
3. Stage only the checked script, source ZIP, runtime ZIP and five model files
   under the immutable artifact prefix; stage the checked microsecond-timestamp
   input Parquet under the separate smoke input prefix. Verify checksums and
   unused output/temp prefixes. Never sync an entire output directory or the
   debugging fixture. See [artifact and runtime validation](cloud-scoring-preparation.md).
4. Obtain approval for one bounded paid smoke run; verify success, counts/IDs,
   features/scores versus the native fixture, label exclusion, partitions and logs.
5. Only then review a fresh full-data input/run-prefix change and estimate. Keep
   curation, baseline, model selection and BQ2 immutable.

Provisioning alone starts no Glue compute. Uploads, storage, logs and paid runs
may incur costs; all remain separately approval-gated. USD 10 is a monthly
notification threshold, not an infrastructure ceiling or lifetime project budget;
the course budget remains USD 250.

## Authenticated preparation follow-up — 2026-10-05

Read-only STS confirmed account `588738581042`. The existing credential process
needs both `AWS_REGION` and `AWS_DEFAULT_REGION` set to `ap-southeast-1` in the
calling process; no credentials or persistent AWS configuration were changed.

Terraform initialized a separate local backend at
`C:/Users/lowth/AppData/Local/IS459-G3/scoring.tfstate`. Validation and all five
mocked tests passed again. The authenticated enabled plan is saved privately as
`scoring.tfplan`, with `scoring-plan.json` beside it: **9 creates, 0 updates,
0 deletes** (job, two roles, two inline policies, two log groups, failure rule
and target). Nothing was applied. This new-stack plan does not verify that
proposed names are unused across every service or that runtime trust works.
The Glue job lookup returned job-not-found; artifact, input, output and temporary
prefix probes each returned zero objects. Both proposed IAM roles and the
failure rule were absent, and the scoring log-group prefix returned no groups.
Recheck immediately before apply/uploads.

The exact nine-file staging manifest is the gitignored
`output/cloud-scoring-verified-20261005/upload-review.json`. It records source
paths, destination keys, byte sizes and SHA256 hashes. It includes only the job
script, source ZIP, runtime ZIP, five frozen model files and verified input
Parquet; parity references and debugging fixtures are excluded. Total upload:
114,476,564 bytes. Hashes must be checked again before any upload.

### Trust investigation result: still unresolved

Checked AWS's [Glue role setup](https://docs.aws.amazon.com/glue/latest/dg/create-an-iam-role.html),
[IAM integration](https://docs.aws.amazon.com/glue/latest/dg/security_iam_service-with-iam.html)
and [policy examples](https://docs.aws.amazon.com/glue/latest/dg/security_iam_id-based-policy-examples.html).
These do not establish that Spark job role assumptions supply the exact
`job/is459-g3-score-frozen` SourceArn and SourceAccount required here. Glue
zero-ETL integration and S3 Access Grants examples refer to different resources
and principals and cannot certify this execution-role context. Absence of a
documented example is not proof of non-support.

The strict trust remains unchanged. Do not use IfExists or broaden it as an
automatic workaround. IAM simulation with supplied context would only test
policy evaluation, not whether Glue actually supplies that context. Resolve
through AWS confirmation or an explicitly approved, fail-closed smoke test using
the existing exact conditions. Any alternative trust design requires a separate
review and user decision. A failed role assumption must stop the attempt, with
no automatic retry or policy relaxation.

### Refreshed cost review

AWS Budgets reported actual monthly spend **USD 0.743**, forecast **USD 5.87**,
and alert limit **USD 10**; its last billing update was 2026-10-05 22:48:40 SGT.
These are delayed account-wide billing figures, not reconciled project-only
charges or guaranteed final totals. No Cost Explorer requests were needed.

The AWS Price List API returned Singapore on-demand rates: Glue 5 ETL
USD 0.44/DPU-hour; S3 Standard first tier USD 0.025/GB-month; PUT/COPY/POST/LIST
USD 0.005/1,000; GET/other USD 0.004/10,000; standard log ingestion USD 0.70/GB
and storage USD 0.03/GB-month. The separate Glue 6+ price is not applicable to
the pinned Glue 5 runtime. See [Glue pricing](https://aws.amazon.com/glue/pricing/),
[S3 pricing](https://aws.amazon.com/s3/pricing/) and
[CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/).

Calculated locally: two G.1X workers for ten execution minutes cost
**USD 0.146667 compute**. The staged package retained for one month costs about
USD 0.002665 in S3. An illustrative scenario adding 1,000 PUT/LIST, 1,000 GET,
100 MiB log ingestion and 14/30 month log retention totals **USD 0.224459**,
excluding output/temp storage, notifications, taxes, credits and existing
resources. Log volume and request counts are assumptions, not measured usage;
the timeout is not a guaranteed all-in billing cap. Provisioning starts no run.
No full-data scoring runtime estimate has been validated yet.

Deployment-policy analysis initially encountered an automatic approval rejection:
the latest third-party Autopilot package could transmit account/infrastructure
details externally, and disabling telemetry is not proof of no transmission.
The user subsequently explicitly approved that analysis. The approved command
completed successfully with telemetry disabled and no upload flags; its result
is saved outside OneDrive at
`C:/Users/lowth/AppData/Local/IS459-G3/scoring-deployment-policy-review.json`.
No IAM policy was attached and no resources were deployed.

### Generated deployment policy review

Autopilot returned one identity policy with nine statements and five
WildcardResource warnings. Treat it as an analysis baseline, **not an approved
least-privilege deployment policy**:

- Both PassRole statements use `Resource: "*"`, constrained only by
  `iam:PassedToService` (Glue or EventBridge). They must be restricted to the
  corresponding scoring and failure-delivery roles before attachment.
- The IAM statement includes account-wide user, policy, instance-profile, MFA,
  OIDC, SAML and server-certificate resource patterns, plus unrelated tag/list
  actions and managed-policy attachment/removal. These exceed the nine-resource
  scoring plan; the stack defines only two roles with inline policies.
- EventBridge rule and log-group creation scopes remain region/account-wide
  wildcards instead of the known scoring names. Several tag/list statements
  use `Resource: "*"`; review action-specific resource support rather than
  assuming all wildcard statements are necessary or all can be narrowed.
- The generated policy includes IAM deletion actions even though this plan has
  only creates. It does not grant Glue StartJobRun or S3 artifact uploads;
  deployment, staging, execution and eventual cleanup remain separate scopes.

The generated baseline is preserved. The user's subsequent instruction to
proceed with refinement authorized narrowing that baseline; the following
local candidate selects actions from it and scopes them against AWS's official
authorization reference. This is a refinement, not a new inference of provider
actions from Terraform. Existing short-lived login credentials remain the
current authentication approach.

### Completed local refinement and validation — 2026-10-05

Final candidate, outside OneDrive:
`C:/Users/lowth/AppData/Local/IS459-G3/scoring-deployment-policy-refined-v2.json`.
SHA256: `9b65b6c899cf4143c45f4902cb98b8069383153913c4ab1f643beebdca7ed925`.
Its `.review.json` companion records baseline/plan/reference hashes and removed
actions. Earlier candidate/results are preserved as historical reviews; use v2.

The final candidate has nine statements and removes 30 distinct baseline
actions, including deletion, managed-policy attachment/removal and unrelated
IAM tagging. IAM actions address only the two named roles; each PassRole
statement binds one exact role to its intended service. Job/rule permissions
address their exact names. Log creation/retention/legacy tagging use the named
group ARNs with `:*`; modern tag/list-tag actions use the corresponding ARNs
without that suffix, per [AWS LogGroup ARN guidance](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_LogGroup.html).

Two read-only broad scopes remain deliberately:

- `logs:DescribeLogGroups` has `Resource: "*"` because the action does not
  support resource-level permissions. This lists account log groups; it grants
  no log contents or changes to other log groups.
- `glue:GetUsageProfile` has the account/region `usageProfile/*` scope. AWS's
  [programmatic Glue reference](https://servicereference.us-east-1.amazonaws.com/v1/glue/glue.json)
  lists it among CreateJob authorized actions. No specific usage profile is
  named in this plan, so retaining the generated read dependency avoids assuming
  it is unnecessary. No usage-profile writes or unrestricted Glue access exist.

IAM role creation and PutRolePolicy necessarily allow setting the two roles'
trust and inline permissions; scoping their ARNs does not constrain the policy
contents to Terraform's intended JSON. This is a deployment-operator candidate,
not an enforced permissions boundary. Do not attach it to untrusted automation.

Reproducible offline refinement from the project root:

```powershell
output/ml-venv/Scripts/python.exe scripts/aws/refine-scoring-deployment-policy.py --baseline C:/Users/lowth/AppData/Local/IS459-G3/scoring-deployment-policy-review.json --plan C:/Users/lowth/AppData/Local/IS459-G3/scoring-plan.json --references output/scoring-policy-refinement-20261005 --output C:/Users/lowth/AppData/Local/IS459-G3/scoring-deployment-policy-refined-new.json
output/ml-venv/Scripts/python.exe -m pytest -q tests/test_scoring_policy_refinement.py
```

Use a fresh output filename. Reference snapshots are gitignored public AWS
service-reference JSON files, with hashes in the review companion. The refiner
rejects different accounts/regions, resources/names, non-create actions and
weakened Glue trust, selects only actions already in the Autopilot baseline,
checks AWS resource-type support and makes no network requests or AWS changes.
It is tailored to this nine-resource stack, not a general policy generator.

Verification:

- All **8 local adversarial tests passed**, covering account/region changes,
  extra/renamed resources, deletion/update/replacement and weaker Glue trust.
- AWS Access Analyzer standard ValidatePolicy returned **no findings** for v2;
  result is privately saved as `scoring-deployment-policy-validation-v2.json`.
  No paid custom check was invoked. See [AWS validation guidance](https://docs.aws.amazon.com/IAM/latest/UserGuide/access-analyzer-policy-validation.html).
- Final IAM custom-policy simulations allowed the scoring role only for Glue
  and the failure role only for EventBridge, denied cross-role use and an
  unrelated administrator role, and denied all three roles for Lambda.
  Sixteen named-resource checks denied StartJobRun, S3 PutObject, DeleteRole and
  AttachRolePolicy. Results are the private `scoring-final-*-simulation.json`
  files. Split per-statement inputs preserved the policy union while respecting
  the simulator's per-input size limit; use ResourceSpecificResults for the
  per-ARN decisions rather than the combined decision summary.

These checks evaluate this candidate in isolation. They do not prove the actual
deployment identity's permissions, provider apply success, cleanup coverage or
Glue-populated STS context. No deployment policy was attached. The strict trust
gate above is unchanged; resolving it still requires AWS confirmation or the
explicitly approved fail-closed cloud smoke path.

### Authorized cloud smoke and prepared retry — 2026-10-05

After reviewing the cost, plan, exact uploads and limits, the user instructed
the agent to proceed with strict-trust verification. The stated execution scope
was nine-resource provisioning, the nine-file manifest and one 1,024-row run.
Fresh preflight rechecked hashes, empty destinations, account and runtime limits.

The private `scoring-smoke-approved.tfplan` applied **9 additions, 0 changes,
0 deletions**. All nine S3 objects were uploaded with `If-None-Match: *`, AES256
and SHA256 checksums, then checked with HeadObject. Receipts/version IDs are in
private `scoring-smoke-upload-receipts.json`. No generated deployment policy was
attached; the existing approved CLI login performed the deployment.

Run `jr_fe07e06068eb3d61eaf7f71c25a184d0a7c18996e17fd3ceda8ff74b5c9d3f54`
started 2026-10-05 **23:30:03.840 SGT**, ended **23:31:35.215 SGT**, and reported
**FAILED**, 79 execution seconds, two G.1X workers and a ten-minute timeout.
Error: `ValueError: Artifact manifest mismatch: comparison.json`.
No output objects exist at `scored/smoke-20261005/transactions/`; the failure
occurred before input loading and scoring. No second run was started.

Live trust was checked before starting and remained exact SourceAccount/job
SourceArn. The job downloaded its dependencies and executed the scorer, with
CloudWatch logs saved privately. **Strict-trust compatibility is empirically
verified for this job/account/region**, not a claim for every Glue role use case.
The failure-delivery target is deployed, but SNS receipt has not been verified.

The downloaded S3 `comparison.json` has the expected SHA256
`eba4f4993aa2a26d62c6640814c11ef54d835d141eb552a8784e1ca2a88aed36`.
The original guard used one error message for either symlink rejection or
checksum mismatch, so the error does not establish byte corruption. Glue logs
showed artifacts under `extra-files/` and Spark's localized directory; the
scorer assumed the process working directory. Working-directory symlinks or
localization are suspected; exact root cause is not established by these logs.
Executor connection errors followed the driver's exit and are not the primary
failure. The original objects and failure evidence remain unchanged.

Locally, the default now resolves the manifest through SparkFiles and reads its
canonical directory. Already localized files are not added to Spark again with
conflicting source paths. Explicit local model paths still work. The hash guard
still rejects tampering and symlinks, with distinct messages and expected/actual
hashes for byte mismatches. **12 native targeted tests passed, 1 symlink test
skipped on Windows; all 6 relevant pinned Glue-container tests passed**, including
that symlink test and distributed parity through the default localized path.
The container used offline wheels, no network and no AWS credentials.

Fresh local package: `output/cloud-scoring-retry-prepared-20261005/`.
Frozen model/manifest unchanged; source ZIP SHA256 is
`c75a2558ac400a1668575911370c1896ee259d191b8cbe826c9b003f1b34f153`.
Its `upload-review.json` lists **8 files** at
`scoring-artifacts/frozen-localized-20261005/`; the existing verified input is
reused. The private `scoring-localized-retry.tfplan` updates only the scoring job
and its inline runtime policy: **0 additions, 2 updates, 0 deletions**, to the new
artifact prefix and `smoke-localized-20261005` output/temp prefixes. Trust,
worker/timeout/retry limits, curation and model selection remain unchanged.
All three proposed fresh destinations were empty when checked. This plan was
not applied and the new release was not uploaded.

The failed run's compute estimate is **USD 0.019311** using reported execution
seconds and the verified rate, excluding ancillary charges and billing timing.
This is not a reconciled bill. Retry planning retains the earlier approximately
USD 0.225 illustrative total scenario and ten-minute compute estimate of
USD 0.146667; actual logs, requests and storage may differ.

`scripts/validation/verify-cloud-scoring-smoke.py` is prepared to reconcile downloaded
successful output with native fixtures: unique IDs, all features, label
exclusion, score tolerances, partitions and cohort top-100 order. It requires a
SUCCEEDED run report; it has not verified cloud output from this failed run.
Explanation comparison is reconstructed locally from cloud features and the
frozen model, not claimed as cloud-emitted explanation output.

**Fresh approval is required for the eight-file uploads, two-update plan and one
second paid smoke run.** No automatic retry, trust relaxation, destructive
cleanup, dashboard publication or full-data scoring occurred. A post-apply
plan with the original smoke settings reported no drift. Keep the scoring
backend enabled when checking this live stack; defaulting provision_scoring to
false would propose destruction.

## Approved localization retry and prepared commit fix — 2026-10-06

The user approved the eight-file upload, two-update plan and one retry. All eight
new artifact objects were uploaded with verified bytes, SHA256 and encryption;
the immutable original input was reused. The plan applied **0 additions,
2 updates, 0 deletions**. No trust or worker/time/retry limit changed.

Run `jr_86b9d7e6c34c8b88c2df4f5ae65f0bc27c4fea9620b8ff76edfcb2d0367ef20a`
started **2026-10-05 23:47:36.558 SGT**, ended **23:49:51.248 SGT**, and
reported **FAILED**, **126 execution seconds**. Artifact verification, input
reading, distributed scoring and four Parquet writes completed. Final commit
failed with **S3 ListBucket 403**, through EMRFS `isFolderUsingFolderObject`,
`mkdirs` and `createSuccessMarkerOrEnsureOutputPathExists`. There is no `_SUCCESS`.
The prefix `scored/smoke-localized-20261005/transactions/` remains unvalidated;
do not reuse, promote, publish or delete it without applicable approval.

Downloaded partial files remain outside OneDrive in the private
`scoring-localized-forensics/curated` folder. Diagnostic report:
`output/scoring-localized-forensics-20261005/forensic-parity.json`.
It confirms **1,024 unique rows**, exact source/features, label exclusion,
correct date partitions, **zero absolute score difference**, and matching
cohort top-100 IDs/order. Explanation comparison is reconstructed locally,
not cloud-emitted. Its explicit status is `forensic-parity-only-failed-run` and
`pipeline_passed=false`. The ordinary verifier still requires SUCCEEDED.
Five verifier tests pass, including refusal to promote failed forensic output.

AWS documents [EMRFS directory markers with the `_$folder$` suffix](https://docs.aws.amazon.com/emr/latest/ReleaseGuide/emr-s3a-migrate.html).
The old prefix condition allows only `prefix` and `prefix/*`, excluding sibling
root markers. The failing stack and documented convention support that diagnosis;
the exact denied request prefix was not recorded, so the marker root cause is
an inference pending cloud verification. No additional paid telemetry was enabled.

Prepared `infra/scoring/main.tf` adds exact marker listing and Get/Put/Delete
permission for only the output and temporary roots of a fresh run. No bucket-wide
listing or `prefix*` exception is added. Six mocked infrastructure tests pass;
Access Analyzer ValidatePolicy has no findings. Read-only IAM custom simulations
pass **8 prefix contexts and 15 object/action boundaries**, including denials for
empty/shared prefixes, other runs, marker suffix extensions and input writes.
Simulations do not prove real EMRFS behavior or replace a successful cloud commit.

Private `scoring-commit-retry.tfplan`: **0 additions, 2 updates, 0 deletions**.
SHA256: `d106ebe35041976a2accaedb8e9f3ee106cd655b662db50d00dbc0053ec7a4ae`.
Only job output/temp destinations and the inline runtime policy change. Fresh
run ID: `smoke-commit-20261006`; both destination inventories were empty when
checked. Reuse `scoring-artifacts/frozen-localized-20261005/` and the verified
1,024-row input; **zero uploads**. Two G.1X workers, ten-minute timeout, zero
automatic retries and concurrency one remain fixed. The plan is not applied;
no third run has started. Applying it and starting one paid run require approval.

Retry compute estimate: **USD 0.030800**; the two smoke attempts total an
estimated **USD 0.050111**, excluding ancillary charges and billing lag.
A third run's ten-minute compute estimate remains **USD 0.146667**, plus actual
logs, requests and storage. These are estimates, not reconciled bills or caps.
No full-data scoring, dashboard publication, curation rerun or cleanup occurred.
