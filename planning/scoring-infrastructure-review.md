# Frozen scoring infrastructure review — 2026-10-05

Terraform in `infra/scoring/` validates and passes five mocked planning tests.
No scoring resources have been deployed, artifacts uploaded or jobs started.
The only live change this step was the explicitly requested monthly budget
update from USD 5 to **USD 10**.

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

No manually reconstructed substitute policy was created. Before using a new
deployment identity, refine the tool-generated result against the reviewed plan
and official action/resource support, validate it, and review any remaining
wildcards. Generation does not simulate the actual deployment identity, prove
complete provider permissions, or resolve Glue service trust context. Existing
short-lived login credentials remain the current authentication approach.

Approval remains required for the nine-resource apply, the manifest's exact
uploads and one bounded 1,024-row smoke run. No apply, upload, paid scoring run,
dashboard publication or changes to the existing curation role occurred.
