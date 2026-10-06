# Full-population cloud scoring and dashboard preparation — 2026-10-06

Latest status: the user approved the two updates and one full-data run. The
reviewed plan applied with zero additions/deletions/uploads, live configuration
passed its guards and the post-apply plan showed no changes. Run
`jr_45ea6604efb57fc630a1c76dc4897d5bf770790aaf3fe288ca1e2b52fa502cdc`
started 2026-10-06 05:45:11.889 SGT and **SUCCEEDED** at 05:48:46.115 SGT,
with 205 execution seconds and 411 reported DPU-seconds. All **5,078,345** rows
passed feature, identity, partition and frozen-score reconciliation. Maximum
absolute score difference is `1.1920928955078125e-7`, within the declared
`rtol=1e-6, atol=1e-7`. The real encrypted `_SUCCESS` exists. The cloud-backed
dashboard and four-file local release are complete and browser-checked.
No dashboard publication occurred. See [result metadata](../../evidence/full-cloud-scoring-result-20261006.json).
Sections below retain the original review and approval scope.

Subsequent separately approved hosting: the verified four-file release is now
public at [d1n61tlw9pcrog.cloudfront.net](https://d1n61tlw9pcrog.cloudfront.net/).
See [publication result](../../evidence/dashboard-publication-result-20261006.json). The original
full-scoring result JSON remains the historical local-export acceptance record.

## Exact proposed AWS scope

Private `C:/Users/lowth/AppData/Local/IS459-G3/scoring-full-reviewed.tfplan`:
**0 additions, 2 updates, 0 deletions, 0 uploads**.
SHA256: `e247d4a724513d19242ef690736b0de8fa50764e59fdbadb0e54d06f5074c0ba`.

- Point the existing scoring job at the verified full curation output:
  `curated/full-optimized-20261005/transactions/`, expecting **5,078,345 rows**.
- Use fresh run ID `full-frozen-20261006`, output
  `scored/full-frozen-20261006/transactions/` and matching temporary prefix.
- Update the inline role policy only to substitute those exact input/output/temp
  prefixes. The actions, marker exceptions, artifact scope and trust stay fixed.
- Start **one** full inference run with two G.1X workers, ten-minute timeout,
  zero automatic retries and concurrency one. Keep explicit Hadoop `_SUCCESS`
  creation enabled. Reuse the exact verified localized artifact release and
  frozen depth-5 model; no uploads or retraining.
- If successful, download that run's Parquet privately, verify every row and
  export the cloud-backed dashboard locally. Publication is a later approval.

At review time, full-scale runtime had not been measured. If a future bounded run fails or times
out, preserve its prefix and evidence; do not retry automatically or reuse it.
The plan changes only the two named resources. No curation job, table, treasury
mart, role trust, worker/time limits, model files or hosting resources change.

## Input and permission evidence

AWS reconfirmed curation run
`jr_6f882796b7df87e127d63cdec9de44039b9dcc3af902eaddf83e8279fdd64486`
for `is459-g3-curate` as **SUCCEEDED**, with the exact intended output prefix.
Its 131 current Parquet objects total **356,618,289 bytes**. Existing full
reconciliation records 5,078,345 unique source indices and transactions, 5,177
positive labels and matching treasury aggregates. The reference used to train
the frozen model is SHA256-anchored.

That older curation run did not emit `_SUCCESS`; a HeadObject check returned
404. Its successful run, Athena checks and saved full reconciliation remain the
input evidence. Do not manufacture a marker or rerun verified curation for this
step. The new scoring output must have its own real marker.

Fresh scoring output/temp inventories were empty when checked. Access Analyzer
ValidatePolicy returned no findings. Six read-only IAM simulations allowed the
new exact input/output/marker prefixes and denied bucket-wide listing, the
earlier invalid full curation prefix and the previous scoring-smoke output.
Private policy, plan, input run, inventories and simulation evidence remain
outside OneDrive. At review time, none of the proposed policy changes were attached;
the approved two-update plan has since applied.

## Local reconciliation and dashboard implementation

`scripts/validation/verify-cloud-scoring-full.py` and `bda_pipeline.cloud_scores` now:

1. Require the successful scoring run, captured live job configuration and
   a marker receipt tied to that run and its exact S3 bucket/key. Check full
   expected population, the explicit marker setting and frozen manifest hash.
2. Hash all typed, ordered input feature values with SHA256 per source index.
   Check unique contiguous IDs, exact bank/account strings, finite features,
   every cloud feature fingerprint and every date partition. Reject labels
   or unexpected output columns.
3. Compare **every** cloud score with frozen native inference in batches, with
   `rtol=1e-6`, `atol=1e-7`. Require complete coverage, finite [0,1] values and
   unchanged source/output files during verification.
4. Save a SHA256-checked, index-addressed `.npy` score cache without pickle,
   plus `verification.json` containing source/model/output provenance.

At 5,078,345 rows the fixed feature-fingerprint, score and coverage arrays use
about **198.57 MiB**, plus a 100,000-row batch and model/runtime memory. These
are calculated array sizes, not measured peak process memory. Fingerprints on
the actual successful 1,024-row AWS smoke match the frozen native reference;
a 100,000-row native fingerprint batch took 1.246 seconds locally. These checks
do not establish full cloud runtime or full-period score parity.

`scripts/dashboard/build-model-dashboard.py --cloud-verification ...` now consumes that
verified score cache instead of predicting replacement scores locally. The
existing source/model/population guards and all three chronological model/rule
metric comparisons remain. Global period queues use cached scores and the
existing deterministic tie order. TreeSHAP explanations are reconstructed
locally for the selected queues, with that origin stated explicitly. A browser
method note distinguishes cloud inference from local export/explanation work.
Rule results and both treasury data structures are preserved exactly.

**29 targeted tests pass**, including reorder/batch coverage, duplicate/missing
rows, label exclusion, changed features/scores/partitions, wrong population,
failed runs, marker receipts, cache tampering and dashboard integration. The
dashboard test forbids local prediction while consuming cloud scores, checks
all three queues/metrics and exact preservation of rules and BQ2. The existing
release/privacy tests also pass. No dashboard claiming full cloud scores was
built from invented data; the actual full-data export subsequently completed
from the approved successful run.

## Implemented commands after a successful approved run

Capture live job configuration before starting the approved run. Preserve its
SUCCEEDED report, download only its Parquet to a fresh private directory, and
save a marker receipt containing `job_run_id`, `bucket`, `key` and `metadata`
(the actual S3 HeadObject response). The receipt must describe
`scored/full-frozen-20261006/transactions/_SUCCESS`, not a previous smoke marker.

```powershell
output/ml-venv/Scripts/python.exe scripts/validation/verify-cloud-scoring-full.py --curated C:/Users/lowth/AppData/Local/IS459-G3/scoring-full-verification/curated --reference output/cloud-insights-20261005/bq1_scored.parquet --model-dir output/cloud-scoring-retry-prepared-20261005/artifacts --run-report C:/Users/lowth/AppData/Local/IS459-G3/scoring-full-status.json --job-report C:/Users/lowth/AppData/Local/IS459-G3/scoring-full-live-job.json --marker-report C:/Users/lowth/AppData/Local/IS459-G3/scoring-full-marker-receipt.json --output output/scoring-full-verification-20261006
output/ml-venv/Scripts/python.exe scripts/dashboard/build-model-dashboard.py --scored output/cloud-insights-20261005/bq1_scored.parquet --baseline output/cloud-insights-20261005/dashboard/dashboard.json --model-dir output/model-comparison-20261005 --cloud-verification output/scoring-full-verification-20261006/verification.json --output output/cloud-model-dashboard-20261006
output/ml-venv/Scripts/python.exe scripts/dashboard/prepare-dashboard-release.py --source output/cloud-model-dashboard-20261006 --output output/cloud-dashboard-release-20261006
```

Those three output directories now exist from the completed verification and
export. For future exports, use fresh output directories. The release command only
packages allowlisted files locally; it does not deploy, upload or publish.
If full-period metrics or queues differ, stop export and investigate instead of
retuning on test results. Neither ranking score is a laundering probability.

## Cost and approval

AWS Budget snapshot: **USD 0.936 actual**, USD 5.87 forecast, USD 10 monthly
alert, last updated **2026-10-06 00:26:52.704 SGT**. This is an account-wide,
delayed billing view, not a current project invoice or hard cap; the course
budget remains USD 250.

At the verified Singapore Glue 5 rate of USD 0.44/DPU-hour, the ten-minute
two-worker compute estimate is **USD 0.146667**. An illustrative incremental
scenario adds 1 GB new S3 output for one month, 100 MiB log ingestion/storage
for one month, 1,000 PUT/LIST and 1,000 GET requests: **USD 0.248356 total**.
This uses the existing verified AWS price snapshot; it is not a guaranteed
maximum, billing reconciliation or prediction of actual output/log size.
Existing inputs and artifacts are reused; no additional artifact upload is
planned. Snapshot and calculation metadata are in
`planning/evidence/full-cloud-scoring-preparation-20261006.json`.

The workspace instructions require explicit approval for deployment and paid
execution. Proposed approval covers the **two updates, one full-data run,
private download, full reconciliation and local dashboard/release export**.
It does not cover retries, hosting/publication, curation reruns or cleanup.

## Completed approved execution and local acceptance

The exact saved plan applied **0 additions, 2 updates, 0 deletions, 0 uploads**.
The post-apply plan showed no changes, and the live role retained exact
SourceAccount and scoring-job SourceArn trust. Only one full scoring run started.
It produced **39 Parquet objects, 235,123,723 bytes**, covering September 1–18,
plus its genuine zero-byte AES256 `_SUCCESS` marker. Run/configuration, marker,
inventory, logs and Terraform evidence remain outside OneDrive in the private
project evidence directory; downloaded scored Parquet remains there too.

The full verifier passed every ordered typed feature fingerprint, complete and
unique source IDs, expected label-free schema, date partitions, finite score
range and frozen-model comparison. Maximum absolute score difference was
`1.1920928955078125e-7`, within the declared relative/absolute tolerance.
The score-cache SHA256 is
`644a7b68dc5d4aa23026578a06b5ecd1cc929cab1f8b547a6fa89e897c2884d1`.

The dashboard consumes that checked cache. Validation/test/tail metrics and
top-100 transaction order match the native frozen snapshot, with identical
displayed explanation text; original rule and treasury structures are exactly
unchanged. Model test precision@100 remains **44%**, versus **0%** for rules on
this synthetic evaluation period. Scores are rankings, not calibrated probabilities;
the sparse tail is not representative. TreeSHAP and export ran locally.

Browser checks verified model/rule switching, validation/test period switching,
transaction evidence and treasury all-days/daily selection; no console errors
were reported. The local preview is `http://127.0.0.1:4176/`.
The four allowlisted release assets total **620,811 bytes**, and every release
hash/size matches the manifest. The manifest is outside the web root and states
`prepared-not-published`; public-demo approval is false. Hosting is undeployed.

Compute estimate using the reported **411 DPU-seconds** at the reviewed
USD 0.44/DPU-hour rate is **USD 0.050233**. This excludes storage, requests,
logs and other services and is not a reconciled bill. The earlier ten-minute
scenario above remains the pre-run estimate.

Machine-readable metadata, full-run/cache/dashboard/release hashes and acceptance
checks: [full-cloud-scoring-result-20261006.json](../../evidence/full-cloud-scoring-result-20261006.json).
The preparation JSON remains the historical pre-approval snapshot.
No additional run, retraining, publication or cleanup was performed.
