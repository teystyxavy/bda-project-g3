# Cloud scoring implementation gate

Latest status: the approved fourth smoke **SUCCEEDED** on 2026-10-06 in 120
execution seconds after explicitly enabling Hadoop success markers. The real S3
`_SUCCESS` exists; ordinary reconciliation passed all 1,024 rows with zero score
difference, exact features/identities, correct partitions and matching top-100
order. Two updates applied, zero uploads. Seven infrastructure tests pass and the
post-apply plan showed no changes. See [verified smoke evidence](scoring-success-marker-review.md).
This verifies the 1,024-row path, not full-data scoring or cloud-emitted explanations.

Full-data preparation is now complete: a private two-update plan reuses the
verified full curation input and frozen artifacts, with zero uploads and the
same bounded runtime. The all-row verifier and checked cloud-score dashboard
path pass 29 targeted tests. See [full-run approval review](full-cloud-scoring-review.md).
The user approved that plan and one full run. Two updates applied with zero
uploads; the job **SUCCEEDED** in 205 execution seconds. All 5,078,345 rows passed
verification, with maximum absolute frozen-score difference `1.1920928955078125e-7`
within `rtol=1e-6, atol=1e-7`. The real `_SUCCESS` exists. Dashboard export and
four-file release are complete locally; explanations remain locally reconstructed.
See [full result metadata](../../evidence/full-cloud-scoring-result-20261006.json). No publication
occurred during scoring; a separately approved hosting step subsequently
published and verified the release. See [publication result](../../evidence/dashboard-publication-result-20261006.json).

Earlier status: the approved localization retry deployed two updates, staged eight
verified artifacts and ran once. All 1,024 cloud scores matched the native fixture
exactly, but Glue FAILED while committing output (S3 ListBucket denied). Partial
output is forensic evidence only. A narrow EMRFS marker fix, six infrastructure
tests and 23 IAM boundary checks pass. A fresh two-update plan reuses those
artifacts without uploads; its third paid run awaits approval.
See [smoke evidence and retry review](scoring-infrastructure-review.md).
The existing curation job is unchanged; ML training has only run locally.

## Implemented local path

`jobs/score_transactions.py` verifies an externally supplied model-manifest hash,
checks all frozen model/schema/report hashes, verifies expected input counts and
unique transaction/source indices, and performs `mapInPandas` inference. Worker
model loads are cached; each model uses one CPU thread and Arrow batches are
bounded at 10,000 rows. The result preserves string identifiers, both USD values,
categorical fields and trailing-history features, adds `model_score`, excludes
labels and partitions Parquet by `txn_date`. A fresh output prefix is mandatory;
failed/partial outputs must never be reused. No training, catalog writes, baseline
replacement, treasury mart updates or dashboard publication are performed.

Preparation and local testing:

```powershell
output/ml-venv/Scripts/python.exe scripts/prepare-cloud-scoring.py --model-dir output/model-comparison-20261005 --scored output/cloud-insights-20261005/bq1_scored.parquet --output output/cloud-scoring-new
./scripts/test-cloud-scoring-local.ps1 -PackageName cloud-scoring-new
output/ml-venv/Scripts/python.exe scripts/bundle-scoring-wheels.py --wheels output/cloud-scoring-20261005/linux-wheels/runtime --output output/cloud-scoring-new/runtime.gluewheels.zip
```

The checked package is `output/cloud-scoring-verified-20261005/`. Model manifest
SHA256: `323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f`.
The original `output/cloud-scoring-20261005/fixture/` uses unsupported nanosecond
Parquet timestamps and is **not the approved parity fixture**; retain it only
as debugging evidence. Its downloaded Linux wheels are reusable and are bundled
separately from model/source code. All generated files remain gitignored.

`jobs/scoring-requirements.txt` pins the complete runtime dependency set. Training
used XGBoost 3.4.1, whose wheels require Python >=3.12; Glue 5 uses Python 3.11.
The tested inference candidate is `xgboost-cpu==3.2.0`. Compatibility must be
established empirically for this frozen UBJ model, not assumed for arbitrary
newer models. No retraining or test-based model selection occurs. The runtime
pins pandas 2.2.2, NumPy 2.2.6 and PyArrow 17.0.0 to match the container; deploy
the pure-Python source ZIP, not the native local editable installation, whose
PyArrow minimum is newer. Native and container environments are deliberately
separate. The script downloads Linux wheels using the pinned Glue image, then
installs offline in an ephemeral container with the repository mounted read-only.

The runtime bundle contains nine wheels and is 114,072,959 bytes. Its companion
manifest records package versions and wheel/archive hashes. It contains no test
dependencies or model artifacts. Reuse the frozen archive rather than resolving
dependencies from PyPI during a paid job.

For a future reviewed Glue job, use the source ZIP via `--extra-py-files`, the
five files in `artifacts/` via `--extra-files`, and the `.gluewheels.zip` via
`--additional-python-modules` with `--python-modules-installer-option=--no-index`.
The job then redistributes verified driver artifacts with Spark `addFile`.
Own required arguments are `--INPUT`, `--OUTPUT`, `--MANIFEST_SHA256` and
`--EXPECTED_ROWS`; `--MODEL_DIR` defaults to the driver working directory.
Only approval-gated Terraform may wire private S3 paths and permissions.
See AWS's [library packaging guidance](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-python-libraries.html)
and [extra-files semantics](https://docs.aws.amazon.com/glue/latest/dg/aws-glue-programming-etl-glue-arguments.html).

The local scorer accepts both Spark timestamp and timestamp-without-timezone
columns; parity fixtures use microseconds rather than unsupported nanoseconds.
UTC is used as a deterministic Spark session setting, not a claim about the
dataset's unverified real-world timezone.

## Validation results — 2026-10-05

- Native Windows suite: 32 passed; two Spark modules skipped because native
  PySpark is not installed. The Glue container run covers those modules.
- Pinned Glue 5 Linux image: **44 passed**, including original curation parity
  and frozen-model distributed scoring. Only dependency deprecation warnings.
- 1,024 verified-source cohort rows spanning training, validation, test and tail:
  Linux XGBoost 3.2.0 and Spark Arrow scores match the frozen native XGBoost
  3.4.1 reference within `rtol=1e-6, atol=1e-7`.
- Top-100 cohort IDs and explanation text match exactly; explanation margins
  agree within `rtol=1e-6, atol=1e-6`. These are cohort checks, not new full-period
  precision@100 measurements and not further model selection.
- Parquet round-trip preserves both USD fields, model features and leading-zero
  identifiers; generated date partitions agree with stored timestamps. Output
  excludes labels. Fresh-output and wrong-count gates are tested.
- Direct prediction handles an unseen categorical value using the saved unknown
  feature. Trusted-manifest rejection and invalid/null/numeric/duplicate input
  checks pass. Source/model/schema tamper checks remain in the model suite.
- No BQ2 mart or dashboard file was changed by scoring. No AWS calls or paid jobs
  were made. An actual cloud smoke test remains required for S3/IAM/bootstrap.

## Implementation sequence and remaining gates

1. Implemented locally: package the existing frozen `SavedRanker`, saved feature schema, selection,
   comparison report and UBJ model for a **Linux Glue 5 / Python 3.11** runtime.
   Pin and validate Linux-compatible NumPy, pandas, PyArrow and XGBoost versions;
   the Windows Python 3.14 virtual environment is not a deployable artifact.
2. Implemented locally: separate inference entry point using curated Parquet, exact saved feature
   names/order and hashes. Load once per worker, score batches with bounded CPU
   threads, preserve stable transaction IDs and string bank/account identifiers.
   No fitting, thresholds learned from test data or test-holdout retuning.
3. Passed locally: container testing with the frozen artifact and a small feature
   cohort. Reproduce native scores within documented floating-point tolerance,
   queue tie ordering and signed explanations. Verify prediction without labels,
   artifact tamper rejection and unchanged BQ2 metrics. Do not claim the existing
   Glue curation parity test proves XGBoost runtime compatibility.
4. Prepared locally in `infra/scoring/`: separate Terraform scoring job and narrowly scoped
   model/input reads and fresh output-prefix writes. Keep the existing rule
   baseline and curated data immutable. BQ2 remains full-population, not queue-only.
   Five mocked tests pass; the original stack is now deployed. See
   `scoring-infrastructure-review.md` for empirical strict-trust compatibility,
   the failed first smoke and the locally prepared retry.
5. Review current spend and a bounded smoke-run plan, obtain explicit approval,
   then run one small cohort before any full dataset run. Proposed guardrails:
   two G.1X workers, at most ten minutes, zero retries, one concurrent run. These
   are proposed limits, not a performance claim or guaranteed total-cost cap.
6. Reconcile scored counts and IDs and compare the same validation/test/tail
   ranking metrics before packaging any cloud-generated dashboard release.

Do not use a long-lived SageMaker endpoint merely for this batch demo. Step
Functions and streaming remain later enhancements after manual scoring and
hosting are verified. Neither optional layer is required to answer the business
questions in the current local dashboard.
