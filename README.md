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

Prepare a daily FX CSV with these columns:

```text
txn_date,currency,usd_rate
2022-09-01,Euro,1.0018
```

Each non-USD payment currency and transaction date in the input sample must have
one rate. US Dollar rows automatically use a rate of `1.0`. Then run:

```powershell
python scripts/run-local-pipeline.py --fx-rates dataset/fx_rates.csv --rows 1000
```

The partitioned result is written under `output/curated/transactions/`. Local
outputs and downloaded data are gitignored.

Run the transformation tests with:

```powershell
python -m pytest -q
```
