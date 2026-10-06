# Earlier local development and AWS milestones

These retained instructions document successive milestones. For the current cloud
entry point, setup and measured results, start with the repository README and
planning index. Historical local output paths require retained private artifacts.

## Downloading the dataset

The complete dataset is about 39GB across all variants and is not committed to
this repo. The download script fetches only the two `HI-Small` files used by the
notebook (about 510MB total).

### 1. Get a Kaggle API token

1. Log in to [kaggle.com](https://www.kaggle.com), go to **Settings → API**, and
   click **Create New Token**. This downloads a `kaggle.json` file.
2. Place it where the Kaggle API expects it:
   - Windows: `C:\Users\<you>\.kaggle\kaggle.json`
   - macOS/Linux: `~/.kaggle/kaggle.json`

### 2. Set up the Python environment

A venv (`kaggle_venv/`) is not committed to the repository. If you already
created it, activate it:

```powershell
.\kaggle_venv\Scripts\Activate.ps1
```

If you're setting up fresh instead, create a venv and install the dependencies:

```bash
python -m venv kaggle_venv
kaggle_venv\Scripts\activate      # Windows
pip install kagglehub pandas numpy matplotlib jupyter nbconvert ipykernel
```

Install the local pipeline and test dependencies from the project root:

```powershell
python -m pip install -e ".[dev]"
```

### 3. Download the dataset

```bash
python scripts/ingestion/kaggle-download.py
```

This uses `kagglehub`'s per-file download option and writes the files directly to
`dataset/` at the project root. Existing valid downloads are reused.

### Resulting structure

The complete Kaggle dataset contains 18 files: two illicit-ratio variants (`HI` =
higher illicit ratio, `LI` = lower) × three sizes (`Small`, `Medium`, `Large`),
each with three files:

| File | Contents |
|---|---|
| `{PREFIX}-{SIZE}_Trans.csv` | Transaction log with the `Is Laundering` label |
| `{PREFIX}-{SIZE}_accounts.csv` | Account → Bank / Entity mapping |
| `{PREFIX}-{SIZE}_Patterns.txt` | Ground-truth laundering "attempts" (cycles, fan-out, etc.) |

The downloader selects `HI-Small_Trans.csv` (about 475MB, 5M rows) and
`HI-Small_accounts.csv` (about 34MB). To analyze another variant, select one of
the commented `PREFIX`/`SIZE` options in `scripts/ingestion/kaggle-download.py` and use the
same `PREFIX`/`SIZE` in the notebook config. Set `INCLUDE_PATTERNS = True` only if
the optional laundering-pattern descriptions are needed.

## Local pipeline smoke test

The first implementation milestone processes 1,000 rows locally before any AWS
resources are created. It validates the raw schema, joins account metadata on
`(Bank ID, Account Number)`, creates the initial transaction features, converts
amounts to USD, and writes Parquet partitioned by `txn_date`.

Generate the daily FX CSV from the currencies and date range present in the
transaction file:

```powershell
python scripts/ingestion/fetch-fx-rates.py
```

For a quicker first run, inspect only the same 1,000 rows used by the smoke test:

```powershell
python scripts/ingestion/fetch-fx-rates.py --rows 1000
```

The downloader uses Frankfurter's historical central-bank-backed rates for the
covered fiat currencies. September 2022 rates for Bitcoin, Ruble, and Saudi
Riyal are obtained from Yahoo Finance daily history because Frankfurter has no
queryable records for those three pairs. The output records the source and
actual observation date; weekends use the latest prior observation. Then run:

```powershell
python scripts/local/run-local-pipeline.py --fx-rates dataset/fx_rates.csv --rows 1000
```

The partitioned result is written under `output/curated/transactions/`. Local
outputs and downloaded data are gitignored.

Run the transformation tests with:

```powershell
python -m pytest -q
```

## Local business-question analysis

After installing the package with `python -m pip install -e ".[dev]"`, run:

```powershell
python scripts/ingestion/fetch-fx-rates.py --lookback-days 1 --output dataset/fx_rates_with_history.csv
python scripts/local/run-local-analysis.py --fx-rates dataset/fx_rates_with_history.csv --output output/analysis-run-1
```

Use a new output directory for each run. The runner scans the complete transaction
file in 250,000-row chunks and checks row counts, FX coverage, and account join
coverage. It preserves original identifiers and uses normalized bank keys for
joins and behaviour calculations. No AWS resources are needed.

- BQ2 covers every input transaction: daily directed bank/currency corridors,
  volume rankings, daily bank-pair HHI (0–1), and top-five volume share. High
  concentration is a treasury observation, not an AML risk signal.
- BQ1 defaults to a deterministic approximately 5% sender cohort (hash modulo 20).
  All outgoing rows for selected senders are retained, preserving their history.
  Set `--sender-modulus 1` for full-data scoring, which requires more memory/time.
  These sample metrics and the queue describe the cohort, not all HI-Small.
- Windows exclude the current timestamp and simultaneous peers. P99 amount
  thresholds are fitted only on September 1–6. Reports evaluate September 7–8
  and September 9–10 separately, with precision/recall/lift@100 and average
  precision (stepwise PR area). Labels never enter scoring; equal scores sort
  by timestamp and source row index. No-positive recall is reported as null.
- AML amount features use prior-day FX to avoid same-day closing-price leakage;
  treasury totals use same-day historical valuation. Pre-dataset account history
  is unavailable. Rule weights remain exploratory because the earlier EDA used
  the full dataset. Pattern enrichment and new-counterparty flags are pending;
  the local ML comparison below is now implemented separately.

September 11–18 is reported separately as a sparse tail: the full dataset has
only 1,108 transactions in that period, including 655 laundering labels.
It is unsuitable as the primary holdout for normal-volume operations.

To re-evaluate previously saved features with the current split policy:

```powershell
python scripts/local/evaluate-local-risk.py --input output/analysis-20261004/bq1_scored.parquet --output output/evaluation-new
```

The evaluation command produces a test-period review queue and `evaluation.json`.
See `planning/archive/reviews/local-validation-results.md` for the first full scan and cohort results.

Outputs: `bq1_scored.parquet`, `review_queue.csv` (top 100, without labels),
`bq2_daily_corridors.parquet`, `corridor_rankings.csv`,
`concentration_daily.csv`, and `run_report.json`. The queue is a retrospective
cohort demonstration, not an operational live queue. Raw FX inputs retain source
and observation-date provenance; retain them alongside each run for reproducibility.

## Local dashboards

Build a static snapshot from the revised AML scores and full-population corridors:

```powershell
python scripts/dashboard/build-dashboard.py --scored output/evaluation-20261004/bq1_scored.parquet --corridors output/analysis-20261004/bq2_daily_corridors.parquet --output output/dashboard
python -m http.server 4173 --bind 127.0.0.1 --directory output/dashboard
```

Open http://127.0.0.1:4173/ (not the HTML directly). Use a fresh output directory
for subsequent builds. Substitute your own evaluation and analysis paths as needed.
Stop the local server with Ctrl+C when finished.

The AML view provides chronological evaluation-period selection, alert counts,
score distribution, a top-100 queue with evidence drilldown, CSV export, and
validation-only rule comparisons. Queue rows omit the label. The treasury view
provides all-period or daily bank/currency rankings, volume and concentration
trends, and CSV export. Trends always retain full-period context; the selected
date is highlighted. HHI and shares use all bank pairs, not just the top 15 shown.

Checked-in assets live in `dashboard/`; the exporter writes `dashboard.json`
with source hashes and `validation-diagnostics.json`. There are no external
JavaScript dependencies. This section describes the earlier local static views;
the current full dashboard is deployed through S3/CloudFront. This earlier export represents a sender cohort for BQ1; BQ2 covers all
5,078,345 transactions. The verified-cloud export below covers the full population
for both questions.

## Full-population insights from verified AWS data

After downloading and verifying a successful full Glue run, score its curated
Parquet and export both dashboard views locally:

```powershell
python scripts/dashboard/export-verified-batch.py --curated output/full-cloud-optimized-20261005/curated --verification output/full-cloud-optimized-20261005/parity.json --output output/cloud-insights-new
python -m http.server 4174 --bind 127.0.0.1 --directory output/cloud-insights-new/dashboard
```

Use a fresh output directory. The successful 2026-10-05 export is already saved
under `output/cloud-insights-20261005/`. Open http://127.0.0.1:4174/ while its
local server is running; stop the server with Ctrl+C when finished.

The exporter requires a passed verification report, checks complete unique source
IDs, positive-label counts and treasury count/USD reconciliation, then generates
scores in batches. Training-only amount thresholds and globally ranked top-100
queues preserve the existing chronological policy. The scored Parquet contains
all rows but is not globally sorted; queue CSVs are ranked and omit labels.
Artifacts include `bq1_scored.parquet`, `bq2_daily_corridors.parquet`, corridor
rankings, concentration, period-specific review queues, `run_report.json`, and
the static dashboard folder. Outputs remain gitignored.

[Full-population validation](cloud-insights-validation.md) records
5,078,345 scored rows and browser checks. The exploratory baseline finds two
labelled positives in the validation top 100 and zero in the test top 100;
it is not a validated production AML model. No extra AWS jobs are needed for this
export. The local ML comparison, separately verified full cloud scoring and
public dashboard hosting are now complete; see the deployment evidence below.

## Local model comparison

Create an isolated environment for the optional ML dependencies:

```powershell
python -m venv output/ml-venv
output/ml-venv/Scripts/python.exe -m pip install -e ".[dev,ml]"
output/ml-venv/Scripts/python.exe -m pytest -q
output/ml-venv/Scripts/python.exe scripts/modeling/compare-local-model.py --scored output/cloud-insights-20261005/bq1_scored.parquet --snapshot output/cloud-insights-20261005/dashboard/dashboard.json --output output/model-comparison-new
```

Use a fresh output directory. The input must match the full-population export's
SHA256 manifest. Two CPU histogram XGBoost candidates (depths 3 and 5) train only
on September 1–6, with at most 120 rounds, four threads and validation-only early
stopping. Exact validation average precision selects the candidate; precision@100
breaks ties. `selection.json` is saved before test data is loaded. The selected
model is evaluated against the same rule baseline on validation, test and sparse
tail; no test-set tuning or automatic promotion occurs.

Features use prior-day USD amounts, strictly trailing history, current-time hour,
self-transfer/currency-mismatch flags and training-only categorical vocabularies.
IDs, labels, absolute dates and same-day FX valuation are excluded from model
inputs. Class weights come only from training labels. Weighted outputs are ranking
scores, **not calibrated laundering probabilities**.

Outputs include native `.ubj` model files, `features.json`, `selection.json`,
`comparison.json`, and label-free top-100 model queues with signed TreeSHAP margin
contributions. Load a model with XGBoost and apply its saved feature schema; do
not unpickle arbitrary artifacts. The original dashboard build remains the rule
baseline; the model-enabled build below preserves it as a comparison. Training and comparison run locally,
without AWS charges.

[Model comparison results](local-model-validation.md): the selected
depth-5 model finds 44 labelled positives in the main test top 100 versus zero
for the rule baseline. This is an exploratory synthetic-data result, not a
production AML validation. The saved 2026-10-05 run is under
`output/model-comparison-20261005/`.

## Dashboard with the frozen model

Reuse the saved model without fitting or tuning again:

```powershell
output/ml-venv/Scripts/python.exe scripts/dashboard/build-model-dashboard.py --scored output/cloud-insights-20261005/bq1_scored.parquet --baseline output/cloud-insights-20261005/dashboard/dashboard.json --model-dir output/model-comparison-20261005 --output output/model-dashboard-new
python -m http.server 4175 --bind 127.0.0.1 --directory output/model-dashboard-new
```

Use a fresh output directory. The existing built preview is at
`output/model-dashboard-20261005/`, served at http://127.0.0.1:4175/ while its
local server is running. Stop the server with Ctrl+C when finished.

The build verifies source, model, selection and feature-schema hashes; reloads
the frozen model; and reproduces all three period metrics before exporting.
`SavedRanker.predict()` also works on feature rows without evaluation labels.
Both queues and a side-by-side comparison are available. Model scores use 0–1,
rules use 0–100, and neither is a probability. Model evidence uses readable feature
names with signed margin contributions; synthetic bank/account strings retain
leading zeros. CSV queues omit labels. Rule alert charts remain explicitly
rule-only; no model threshold or model-alert counts are invented. Treasury data
and original rule results are preserved unchanged. See
[dashboard integration checks](model-dashboard-validation.md).

## AWS batch foundation

Dashboard hosting is now deployed in the separate `infra/hosting/` stack.
The verified static synthetic-data dashboard is public at
[d1n61tlw9pcrog.cloudfront.net](https://d1n61tlw9pcrog.cloudfront.net/).
The origin bucket remains private. An allowlisted four-file release can be built with:

```powershell
output/ml-venv/Scripts/python.exe scripts/dashboard/prepare-dashboard-release.py --source output/model-dashboard-20261005 --output output/dashboard-release-new
```

The approved four-file cloud-backed release is published. See [hosting and cost review](dashboard-hosting-review.md)
and the [current nine-resource publication review](dashboard-publication-review-20261006.md)
for the private-origin/public-viewer distinction and approval gates.
Staging added nine resources, then a guarded update enabled only the distribution.
All four uploads and public assets match the approved hashes. HTTPS/security,
origin-denial and live browser checks passed; the post-apply plan shows no drift.
See [publication evidence](../../evidence/dashboard-publication-result-20261006.json).
Cost examples assume no free allowance; public traffic incurs usage-dependent costs.
Cloud scoring now has a separate local inference entry point, frozen artifact packaging and
Linux-container parity tooling; [cloud scoring status and commands](cloud-scoring-preparation.md)
describe the runtime compatibility checks. The scoring stack is now deployed.
The approved fourth 1,024-row smoke **SUCCEEDED** on 2026-10-06 in 120 execution
seconds after explicitly enabling Hadoop success markers. S3 `_SUCCESS` exists;
all scores match the frozen native fixture exactly, with matching features,
identities, partitions and cohort top-100 order. Strict Glue trust worked.
See [verified smoke evidence](scoring-success-marker-review.md).
Earlier failed prefixes remain forensic evidence only. The separately approved
full-data run **SUCCEEDED** in 205 execution seconds. All 5,078,345 cloud rows
passed feature/identity/partition checks and frozen-score comparison within
`rtol=1e-6, atol=1e-7` (maximum absolute difference `1.1920928955078125e-7`).
The cloud-backed dashboard is `output/cloud-model-dashboard-20261006/`;
its four-file release is `output/cloud-dashboard-release-20261006/site/`.
Model explanations and dashboard export are reconstructed locally; model scores
come from the verified AWS output. Rules and treasury aggregates are unchanged.
29 targeted tests pass, and the browser preview checks passed without errors.
See [full-data scoring review and evidence](full-cloud-scoring-review.md).
The separately approved public dashboard release is now deployed and verified.
Model training, explanation reconstruction and export remain local; curation
and full model inference ran in AWS. This is a static snapshot, not live alerting.

```powershell
output/ml-venv/Scripts/python.exe -m http.server 4176 --bind 127.0.0.1 --directory output/cloud-model-dashboard-20261006
```

Open `http://127.0.0.1:4176/` after starting the local preview.

`infra/scoring/` now defines the separate, opt-in scoring job and run-scoped IAM
permissions. It validates locally and passes seven mocked tests; see the
[scoring infrastructure review](scoring-infrastructure-review.md) for
trust evidence, artifact staging and the historical failed smoke attempts.
The user updated the live monthly budget alert to USD 10 on 2026-10-05; the local
default and validation maximum are also 10. This is not a hard AWS spend cap.

Start with [local AWS login](../../runbooks/aws-login.md). The
`scripts/aws/configure-aws.ps1` helper opens AWS browser authentication and saves
project settings outside the repository; enter passwords only on the AWS page.

`infra/terraform/` defines the private S3 bucket, Glue sample job, projected
catalog table, Athena workgroup and saved queries, failure notifications and
budget. `jobs/curate_transactions.py` implements PySpark curation with account
and FX validation. `scripts/validation/prepare-aws-smoke.py` creates 1,000 raw sample rows,
the required account subset and a local reference result without AWS calls.

Follow [the AWS batch runbook](../../runbooks/aws-batch-runbook.md) for local parity
tests, configuration, approval-gated deployment, reconciliation and cleanup.
Terraform validation and three mocked planning tests pass. All 30 Python tests
pass in the official Glue 5 container, including Spark parity; run them with
`.\scripts\validation\test-glue-local.ps1` after preparing the sample. An authenticated
[deployment plan](aws-plan-review.md) was applied with user approval.
[Deployment checks](../../aws-deployment-status.md) confirm 25 resources and
no drift. The approved 1,000-row cloud smoke run and both Athena queries succeeded;
all 22 original curated fields match the local reference. The extended job now
adds stable source-row IDs and opt-in exact rolling behaviour features; Spark
parity tests pass. The user approved a bounded full-data run; see the deployment
status document for its results: the first run timed out, but the optimized
retry succeeded in 386 execution seconds and passed full reconciliation.
The original partial output remains invalid. Full-data verification uses
`scripts/validation/verify-aws-full.py` and `sql/reconcile-full.sql` against the separate
`full_transactions` catalog table. Full-population rule scoring and dashboard
export from the verified cloud data work locally; full AWS scoring and dashboard
publication subsequently completed and passed verification. ML training remains
local. The first bounded orchestration workflow is now deployed and fully reconciled;
the bounded streaming replay is verified and its consumer is disabled.

`infra/orchestration/` adds an opt-in Standard Step Functions workflow for frozen
scoring and directed treasury queries. It checks source/job contracts and fresh
output before compute, preserves failed output, and stops before reconciliation
and publication. The approved execution succeeded: all 5,078,345 scores and
1,082,185 daily bank/currency corridor rows passed local reconciliation.
Thirty-nine targeted Python tests and five mocked Terraform tests pass;
post-deployment plans show no drift. No schedule or new dashboard publication. See the
[orchestration review](orchestration-review-20261006.md) for exact plans,
costs, acceptance criteria and the optional disabled one-off Scheduler.

Prepare an unapproved execution input and definition locally in a fresh directory:

```powershell
python scripts/aws/prepare-orchestration.py --run-id full-orchestrated-20261006 --output output/orchestration-review-local
python -m pytest tests/test_orchestration.py -q
```

These commands do not deploy resources or start AWS jobs.

[Local performance validation](glue-performance-validation.md) records
the queue-based optimization: full history features took 38.6 seconds locally,
with exact agreement across the saved sender cohort. The optimized version is
now deployed. The complete optimized local job finished in 163
seconds and passed full ID/corridor reconciliation plus cohort feature parity.
The approved cloud retry also passed these checks, including Athena.
Further paid runs still require approval and a fresh output prefix.

## Verified bounded streaming demo

The optional streaming demo now has a tested local producer, frozen-model Lambda
image, transactional account state and S3 audit, plus opt-in Terraform. The final
Linux image reproduces all 884 selected events across 32 complete sender histories.
Fourteen streaming resources are deployed. The single approved replay produced
884 matching cloud results and 32 exact sender histories, with no processing
errors, throttles or failure archive records. The consumer is verified Disabled
and Terraform shows no drift. Read the [completed result](streaming-demo-result-20261006.md)
and [local commands](../../../streaming/README.md) for retained evidence and verification.
The provisioned shard remains billable until approved cleanup. Processing-failure
notifications are scoped separately from model ranking; the published dashboard
remains a static batch snapshot.
