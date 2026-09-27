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

A venv (`kaggle_venv/`) is already set up in this project with `kagglehub`,
`pandas`, `numpy`, and `matplotlib` installed. Activate it:

```powershell
.\kaggle_venv\Scripts\Activate.ps1
```

If you're setting up fresh instead, create a venv and install the dependencies:

```bash
python -m venv kaggle_venv
kaggle_venv\Scripts\activate      # Windows
pip install kagglehub pandas numpy matplotlib jupyter nbconvert ipykernel
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
