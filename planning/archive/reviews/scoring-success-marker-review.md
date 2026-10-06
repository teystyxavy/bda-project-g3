# Scoring smoke verified and commit history — 2026-10-06

Subsequent full-data result: the separately approved run succeeded in 205
execution seconds with a real encrypted `_SUCCESS`; all 5,078,345 rows passed
verification. See [full scoring review](full-cloud-scoring-review.md).
The sections below describe the preceding smoke run and commit diagnosis.

Latest result: the approved fourth smoke **SUCCEEDED** in 120 execution seconds.
The real S3 `_SUCCESS` marker exists, and ordinary fixture reconciliation passes
all 1,024 rows with zero score difference. See the final section and
[saved result metadata](../../evidence/scoring-smoke-result-20261006.json). Earlier sections
retain the diagnosis and approval review before this successful execution.

The approved third smoke FAILED after 105 execution seconds at the same EMRFS
folder check. The exact two root-marker permissions were insufficient. All
1,024 partial scores match the native fixture exactly, but the failed prefix has
no `_SUCCESS` and is not valid pipeline output. This describes the third run;
the successful fourth run uses a separate fresh prefix.

## Completed approved work

Private plan `scoring-commit-retry.tfplan` applied **0 additions, 2 updates,
0 deletions**, with no uploads. Exact account/job trust and two G.1X workers,
ten-minute timeout, zero retries and concurrency one were verified. The immediate
post-apply plan showed no changes.

Run `jr_0c4985c9956124edbcd8cb6b3498f0c2d32f21a9c7deb2c6a2d3104b64befff2`:
**2026-10-06 00:08:57.995–00:10:50.343 SGT**, **FAILED**, **105 execution seconds**.
The failure stack calls `PathOutputCommitter.createSuccessMarkerOrEnsureOutputPathExists`
at line 101, then EMRFS `mkdirs`, `getFileStatus`, `isFolderUsingFolderObject`
and S3 ListObjectsV2, which is denied. The exact denied listing prefix remains
unknown; the previous root-marker-only diagnosis was insufficient.

Four partial Parquet files (94,511 bytes total) remain at
`scored/smoke-commit-20261006/transactions/`, without a success marker. Private
downloads are under `C:/Users/lowth/AppData/Local/IS459-G3/scoring-commit-forensics/curated`.
The report `output/scoring-commit-forensics-20261006/forensic-parity.json` confirms
1,024 unique IDs, exact features, excluded labels, correct partitions, zero score
difference and matching cohort top-100 order. Explanations are reconstructed
locally. It explicitly reports `pipeline_passed=false`; preserve this failed
prefix and do not promote or reuse it.

## More specific diagnosis, tested without AWS

The installed, pinned Glue 5 container includes the same
`hadoop-client-api-3.4.1-amzn-0.jar` named in the cloud stack. Inspection of its
`PathOutputCommitter` bytecode shows two final-commit branches:

- `mapreduce.fileoutputcommitter.marksuccessfuljobs=true`: directly create
  `output/_SUCCESS` and close the stream.
- `false`: call `mkdirs(output)`, matching the cloud failure stack.

A filesystem spy executed both branches against that actual library, offline:
the false branch reached an intentionally denied `mkdirs`; the true branch
created only the expected `_SUCCESS` path. Evidence is saved under
`output/scoring-commit-diagnosis-20261006/`. This validates the branch selection;
it does not prove the remote EMRFS create path, which still needs a successful
cloud run and ordinary reconciliation.

[Apache Hadoop documents the success-marker option](https://hadoop.apache.org/docs/stable/hadoop-aws/tools/hadoop-aws/committers.html).
[AWS Glue documents `--conf` for Spark configuration](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-glue-arguments.html).
The prepared Terraform argument explicitly selects:

```text
--conf spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs=true
```

This creates a real commit marker through Hadoop; it does not manually stamp
failed output or weaken the job-success gate. Seven mocked infrastructure tests
pass, including this exact non-overridable configuration. Existing output IAM
already permits `_SUCCESS`; no permissions are expanded by the new plan.

## Approved fourth-smoke scope (review before execution)

Private `scoring-success-retry.tfplan`: **0 additions, 2 updates, 0 deletions**.
SHA256: `7b6b8e08277a932ec25708c8fd025b05c58e7d428dbabc7582952d5f86ebb4e4`.
Plan/state/logs remain under the private IS459-G3 directory outside OneDrive.

- Update the job's output/temp paths and add the explicit marker configuration.
- Update the runtime policy only by substituting fresh run ID
  `smoke-success-20261006`; its actions and scope structure are otherwise identical.
- Reuse the verified 1,024-row input and immutable localized artifact release;
  **zero uploads**, no training or model changes.
- Start **one** fourth smoke run: two G.1X workers, ten minutes, zero automatic
  retries, maximum one concurrent run. Fresh destinations were empty when checked.
- Require SUCCEEDED, an S3 `_SUCCESS` object and ordinary fixture reconciliation
  before calling this path verified. No full-data run or publishing is included.

At review time this plan was prepared, not applied. The user subsequently
approved its deployment and one paid execution, recorded below.

Third-run compute estimate: **USD 0.025667**. All three smoke attempts together:
**USD 0.075778** compute estimate. A fourth run's ten-minute compute estimate is
**USD 0.146667**, plus actual requests, logs and storage. These are not reconciled
bills or hard spending caps. No cleanup, dashboard publication or curation rerun
was performed.

## Approved fourth run — SUCCEEDED and reconciled

The user approved the reviewed two updates and one run. The exact reviewed plan
hash was checked, fresh destinations and absence of active runs confirmed,
and Terraform applied **0 additions, 2 updates, 0 deletions**. **No uploads**.
Live configuration verified the explicit non-overridable success-marker option,
1,024 expected rows, two G.1X workers, ten-minute timeout, zero automatic retries
and concurrency one. The post-apply plan showed **no changes**. Exact Glue
SourceAccount/job SourceArn trust remained unchanged and was verified again.

Run `jr_16bd257e4e249cac514b5088672bac7e04aee598ad08eed033275a4f87d99339`:
**2026-10-06 00:22:07.882–00:24:16.168 SGT**, **SUCCEEDED**, **120 execution seconds**.
The output is `s3://is459-g3-588738581042-ap-southeast-1/scored/smoke-success-20261006/transactions/`.
S3 HeadObject verified the real zero-byte `_SUCCESS`, versioned and encrypted
with AES256. The output inventory contains that marker and four Parquet objects,
94,511 Parquet bytes across four date partitions. Logs, job reports, inventory,
marker metadata and private downloads are preserved outside OneDrive.

Ordinary `scripts/validation/verify-cloud-scoring-smoke.py` verification passed without
`--forensic`. Saved report: `output/scoring-smoke-success/parity.json`; durable
result metadata: `planning/evidence/scoring-smoke-result-20261006.json`. Checks confirm:

- Exactly 1,024 rows, unique transaction IDs and source indices.
- Exact source features/identities and exclusion of evaluation labels.
- Finite scores in [0,1], with **zero absolute difference** from the frozen
  native fixture; these remain ranking scores, not calibrated probabilities.
- Correct dates: 2022-09-01, 2022-09-07, 2022-09-09 and 2022-09-12.
- Matching cohort top-100 IDs and order. Explanation text/margins were
  reconstructed locally from cloud features and the frozen model; the cloud
  scorer itself did not emit explanation output.

Explicit marker creation resolves the observed final-commit failure for this
job. This does not establish which listing prefix the previous EMRFS mkdirs
attempt requested, or whether the exact root-marker policy exception is needed
elsewhere. No permissions were broadened beyond the reviewed run scopes.

This successful run's compute estimate is **USD 0.029333**. The four smoke runs
total **USD 0.105111** estimated compute at the verified Singapore Glue rate,
excluding ancillary charges and billing lag; these are not reconciled bills.
No fifth run, full-data scoring, retraining, dashboard publication, curation
rerun or destructive cleanup occurred. The verified scope is the 1,024-row
cloud inference path; full-data scoring and publication require their own
concrete review and explicit approval.
