# IBM AML Transactions — Big Data Architecture Project

Analysis and pipeline design for the
[IBM Transactions for Anti-Money Laundering (AML)](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
dataset on Kaggle.

## Project layout

```
scripts/    kaggle-download.py, eda.ipynb
planning/   business questions, pipeline plan, architecture diagrams
dataset/    downloaded dataset files (gitignored, see below)
```

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
python scripts/kaggle-download.py
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
the commented `PREFIX`/`SIZE` options in `scripts/kaggle-download.py` and use the
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
python scripts/fetch-fx-rates.py
```

For a quicker first run, inspect only the same 1,000 rows used by the smoke test:

```powershell
python scripts/fetch-fx-rates.py --rows 1000
```

The downloader uses Frankfurter's historical central-bank-backed rates for the
covered fiat currencies. September 2022 rates for Bitcoin, Ruble, and Saudi
Riyal are obtained from Yahoo Finance daily history because Frankfurter has no
queryable records for those three pairs. The output records the source and
actual observation date; weekends use the latest prior observation. Then run:

```powershell
python scripts/run-local-pipeline.py --fx-rates dataset/fx_rates.csv --rows 1000
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
python scripts/fetch-fx-rates.py --lookback-days 1 --output dataset/fx_rates_with_history.csv
python scripts/run-local-analysis.py --fx-rates dataset/fx_rates_with_history.csv --output output/analysis-run-1
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
  the full dataset. Pattern enrichment, new-counterparty flags, and ML are pending.

September 11–18 is reported separately as a sparse tail: the full dataset has
only 1,108 transactions in that period, including 655 laundering labels.
It is unsuitable as the primary holdout for normal-volume operations.

To re-evaluate previously saved features with the current split policy:

```powershell
python scripts/evaluate-local-risk.py --input output/analysis-20261004/bq1_scored.parquet --output output/evaluation-new
```

The evaluation command produces a test-period review queue and `evaluation.json`.
See `planning/local-validation-results.md` for the first full scan and cohort results.

Outputs: `bq1_scored.parquet`, `review_queue.csv` (top 100, without labels),
`bq2_daily_corridors.parquet`, `corridor_rankings.csv`,
`concentration_daily.csv`, and `run_report.json`. The queue is a retrospective
cohort demonstration, not an operational live queue. Raw FX inputs retain source
and observation-date provenance; retain them alongside each run for reproducibility.

## Local dashboards

Build a static snapshot from the revised AML scores and full-population corridors:

```powershell
python scripts/build-dashboard.py --scored output/evaluation-20261004/bq1_scored.parquet --corridors output/analysis-20261004/bq2_daily_corridors.parquet --output output/dashboard
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
JavaScript dependencies. These are local static views, not deployed or live
alerts. BQ1 remains a sender cohort; BQ2 covers all 5,078,345 transactions.
