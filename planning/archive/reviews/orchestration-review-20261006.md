# Reviewed batch orchestration — deployed and fully reconciled

Prepared on 6 October 2026 for account `588738581042`, Singapore. The published
dashboard and previous successful outputs remain the verified project baseline.
The user approved the concrete scope below. Deployment and the single execution
now passed full local reconciliation. This completes the first bounded X-factor 1
implementation; scheduling and streaming remain outside the deployed scope.
See [result metadata](../../evidence/orchestration-result-20261006.json).

## Completed approved execution

Exactly **three resources added, two updated, zero deleted**, with no artifact
uploads. Post-apply plans for both stacks showed no changes. The only execution
was `full-orchestrated-20261006`, started 07:04:12.974 SGT and SUCCEEDED at
07:08:05.443 on 6 October 2026 (232.469 seconds, 18 states entered, no retries).

The Glue run `jr_94598a100c6c9138ada22697eab145b64932aebc9132f6f9cd4227ebebe63211`
succeeded with 180 execution seconds and 361 DPU-seconds. Its real zero-byte
AES256 `_SUCCESS` exists. All **5,078,345** rows passed feature fingerprints,
complete unique identities, date partitions and frozen-score parity. Maximum
absolute score difference is `1.1920928955078125e-7`, within `rtol=1e-6,
atol=1e-7`. The new verified score cache SHA256 equals the previous full-success
cache; no model retuning or dashboard republishing occurred.

Athena query `dd22ffbb-3db0-43b5-87d3-115bbffdfd78` succeeded in 6.312 seconds,
scanning **86,039,814 bytes**. Every **1,082,185** directed bank/currency daily
corridor row reconciled across September 1–18. Counts match exactly; maximum
USD-volume difference is `0.001739501953125`, share difference
`2.345346139520643e-15`, and HHI difference `5.8494875609937935e-15`.
Bank identifiers are preserved as strings, including leading zeros. These are
volume/concentration results, not AML suspiciousness scores.

Read/job/query/marker integrations and exact workflow-role trust worked in this
execution. The failure-notification branch was not triggered and is locally
tested only. No schedule exists. Source capture, run/query history, marker,
private Parquet/CSV downloads and Terraform evidence remain outside OneDrive.
Local verification is under `output/orchestration-verification-20261006/`.
**39 targeted Python tests and five mocked Terraform tests pass.**

Measured usage gives approximately **USD 0.045** for Glue, Athena and Step
Functions: `361/3600 × 0.44`, approximately `87 decimal MB × USD 5/TB`, and
`18 × 0.000025`. The Athena conversion is a rounded decimal-MB scenario;
actual billing remains authoritative. This is not an invoice and excludes
storage/requests, logs, downloads, taxes and other resources. The previously
reviewed USD 0.20–0.30 execution/reconciliation allowance remains a scenario.
The delayed budget snapshot still reports USD 0.936 actual / USD 5.87 forecast,
last updated 00:26:52 SGT, **before this run**; it cannot establish this run's bill.
The monthly alert remains USD 10, not a hard cap.

The following sections preserve the preparation scope, design, tests and cost
review. Their unapproved preparation input remains unchanged as historical evidence.

## Originally reviewed scope — now completed

| Change | Reviewed scope |
|---|---|
| New resources | Three: workflow role, inline policy, Standard Step Functions state machine `is459-g3-reviewed-batch` |
| Existing updates | Two: frozen scorer's output/temp arguments and corresponding run-scoped role permissions |
| Candidate execution | One named `full-orchestrated-20261006`, full 5,078,345-row frozen scoring plus one treasury SELECT |
| Source | Existing verified `curated/full-optimized-20261005/transactions/`; no curation rerun |
| Artifacts | Existing frozen-localized release; no upload, retraining or model selection |
| Publication | No hosting changes, dashboard upload or automatic promotion |
| Scheduling | Zero scheduled resources in both reviewed plans; optional one-off Scheduler remains absent |

The scorer's only changed arguments are `--OUTPUT` and `--TempDir`. Its script,
model manifest, source, row count, worker count, ten-minute timeout, zero retries
and one concurrent run are unchanged. Completed/failed previous outputs are retained.

Private saved plans and state remain in `C:/Users/lowth/AppData/Local/IS459-G3/`:
`orchestration-reviewed-20261006.tfplan`,
`scoring-orchestration-reviewed-20261006.tfplan`, and separate
`orchestration.tfstate` / existing `scoring.tfstate`. Fresh approved plans were rechecked against this exact scope and applied.
Approved plan/apply/post-apply evidence uses corresponding `*-approved-*`,
`*-apply-*` and `*-postapply-*` filenames in the same private directory.
Any later changed deployment or additional paid execution needs a new review.
Do not use orchestration state for another stack.

## What the workflow does

1. Require explicit execution input approval, the reviewed run ID/count, and an
   execution name equal to that run ID. The prepared input has approval **false**.
2. Read the scorer, projected table and Athena workgroup. Reject changed compute,
   source/output/manifest/marker configuration, projection location, encryption,
   enforced result location or the 1 GiB query scan cutoff.
3. Reject any existing scored or temporary output. Prefix checks include the
   root without its trailing slash, so partial output/root markers are detected.
4. Run the frozen Glue scorer and directed bank/currency daily volume query in
   parallel. Neither compute task retries automatically. Athena uses a request
   token derived from the execution ARN. Standard's fixed execution name also
   prevents duplicate starts for this reviewed run during its name-retention period.
5. Check the scorer's actual zero-byte AES256 `_SUCCESS` object, then return
   job/query references with `business-outputs-ready-reconciliation-required`.
6. Route catchable read/compute failures to the existing SNS failure topic and
   finish FAILED. Preserve partial output; investigate before reviewing a fresh run.

Read operations alone have bounded retries. Notification failures are retained
in execution history and lead to FAILED. `States.Runtime`, payload-limit failures
and overall execution timeouts cannot be covered by `States.ALL`; inspect
Standard execution history and service metrics for these. There is no new
execution-status notification rule. Cancellation of a parallel sibling is best
effort: inspect Glue/Athena status after failures rather than assuming it stopped.

This is a deliberately bounded extension of the verified manual batch path.
Ingestion, curation, model training, explanation export, full reconciliation and
publication are not automated by this first state machine. Future scheduling
must review a fresh source/output contract; replaying historical data daily would
spend money without adding information.

## Verification completed

- 31 targeted Python tests passed, including 16 orchestration tests: request and missing/changed contract rejection, preserved
  output, success markers, failure paths, directed FX-normalized daily corridors,
  concentration and unsafe run IDs.
- Five mocked Terraform tests passed; validation and formatting passed. Default
  creates nothing; manual provision creates no schedule; optional schedule stays
  disabled with fixed execution name, no delivery retry and schedule-group trust.
- AWS `ValidateStateMachineDefinition`: **OK**, zero diagnostics, not truncated.
- AWS IAM Access Analyzer: zero policy findings. Runtime permissions use exact
  job/catalog/workgroup/topic resources and reviewed S3 prefixes; no wildcard
  resource, dashboard access, data deletion or model writes.
- Authenticated plans: exactly three additions plus two in-place updates, no
  destruction. Python-rendered definition equals the Terraform definition.
- Read-only AWS checks confirmed the projected source/workgroup and both empty
  candidate output prefixes. An offline check of captured live settings also
  confirms every job guard passes with only the two planned argument updates. At preparation, the live scorer used the earlier successful output and would
  fail the new contract check until the approved updates; it now uses the new prefix.

The IAM simulator returned a generic Glue resource-template denial rather than
evaluations for the supplied concrete job ARNs. That check is inconclusive and
is not counted as permission validation. Service Authorization Reference supports
job-scoped Glue actions; actual role assumption and integration permissions still
need verification in the approved execution. SQLite fixture execution validates
the SQL arithmetic; it does not replace Athena engine acceptance.

Prepared non-secret evidence is under `output/orchestration-definition-20261006/`:
ASL, unapproved input, runtime policy, AWS validators, plan summary and official
Singapore price records. These generated artifacts are gitignored. Source lives
in `infra/orchestration/`, `src/bda_pipeline/orchestration.py`,
`scripts/prepare-orchestration.py` and `sql/full-orchestrated-corridors.sql`.

## Cost review for one execution

Official Singapore price-list records retrieved on 6 October: Glue 5 standard
ETL **USD 0.44/DPU-hour**, Athena **USD 5/TB scanned**, Standard Step Functions
**USD 0.000025/transition**. No free-tier deduction is assumed.

| Scenario | Approximate charge |
|---|---:|
| Glue matching previous 411 DPU-seconds | USD 0.05023 |
| Glue at configured two workers × ten-minute timeout | USD 0.14667 |
| Athena scanning 1 GiB at the cutoff | USD 0.00488 |
| Allowance of 60 state transitions | USD 0.00150 |
| Ten-minute compute + cutoff + transitions | **USD 0.15305** |

The previous full run was 205 execution seconds; matching it would put the
listed compute/query/transitions near USD 0.05661. These are scenarios, not a
hard spending cap: Athena cancellation/scan cutoffs and task cancellation are
best effort, and S3 requests/storage, Glue logs, SNS, verification downloads,
taxes and unrelated resources add charges. Allow roughly **USD 0.20–0.30** for
the single run plus reconciliation, subject to actual output/download/log sizes.
Retained output adds storage while it remains. Existing hosting costs continue.
The USD 10 monthly alert is awareness; the course budget remains USD 250.

Pricing references: [Glue](https://aws.amazon.com/glue/pricing/),
[Athena](https://aws.amazon.com/athena/pricing/),
[Step Functions](https://aws.amazon.com/step-functions/pricing/).
Integration references: [Glue sync](https://docs.aws.amazon.com/step-functions/latest/dg/connect-glue.html),
[Athena sync](https://docs.aws.amazon.com/step-functions/latest/dg/connect-athena.html),
[error handling](https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html),
[catalog permissions](https://docs.aws.amazon.com/athena/latest/ug/fine-grained-access-to-glue-resources.html),
[Scheduler trust](https://docs.aws.amazon.com/scheduler/latest/UserGuide/cross-service-confused-deputy-prevention.html).

## Acceptance procedure — completed for this run

Capture execution/job/query settings, transitions, billing statistics and actual
marker/object receipts. Reuse `scripts/verify-cloud-scoring-full.py` with the new
run evidence to reconcile every identity, feature, score and partition against
the frozen model. Download the Athena result and compare daily bank/currency
counts, USD totals, shares and HHI with local aggregates from verified curated
data. Success requires both business outputs to reconcile; a SUCCEEDED state
alone is insufficient. A failed run gets no automatic replacement paid execution.
Do not republish the dashboard in this scope.


The treasury reconciliation tooling is implemented by
`scripts/verify-orchestrated-corridors.py` and `bda_pipeline.treasury_verification`.
It hashes the reference/result/query/SQL, checks the successful reviewed query,
and compares each directed corridor and daily metric independently in batches.
Athena omits the final SQL semicolon in GetQueryExecution; the verifier permits
only that delimiter and exterior whitespace difference. Eight tests cover
batching, leading-zero collisions, zero-volume days, population and key/metric
tampering, and narrow SQL delimiter normalization. No replacement query was run.

To repeat local reconciliation, choose fresh report/output paths:

```powershell
output/ml-venv/Scripts/python.exe scripts/verify-orchestrated-corridors.py --reference output/cloud-insights-20261005/bq1_scored.parquet --result C:/Users/lowth/AppData/Local/IS459-G3/orchestration-verification-20261006/treasury-corridors.csv --query-report C:/Users/lowth/AppData/Local/IS459-G3/orchestration-treasury-query-20261006.json --output output/orchestration-treasury-repeat.json
output/ml-venv/Scripts/python.exe scripts/verify-cloud-scoring-full.py --curated C:/Users/lowth/AppData/Local/IS459-G3/orchestration-verification-20261006/curated --reference output/cloud-insights-20261005/bq1_scored.parquet --model-dir output/cloud-scoring-retry-prepared-20261005/artifacts --run-report C:/Users/lowth/AppData/Local/IS459-G3/orchestration-scoring-status-20261006.json --job-report C:/Users/lowth/AppData/Local/IS459-G3/orchestration-live-job-20261006.json --marker-report C:/Users/lowth/AppData/Local/IS459-G3/orchestration-scoring-marker-receipt-20261006.json --output output/orchestration-score-repeat
```

These commands consume retained outputs locally and start no cloud tasks.
