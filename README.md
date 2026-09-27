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

The dataset (~39GB across all variants) is not committed to this repo — you need
to download it locally before running `scripts/eda.ipynb`.

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

This uses `kagglehub` to download and cache the dataset outside the project
(typically `~/.cache/kagglehub/datasets/ealtman2019/...`), and prints the local
cache path.

### 4. Move the files into `dataset/`

The notebook and planning docs expect the raw files directly under `dataset/` at
the project root. Move everything from the printed cache path into `dataset/`:

```powershell
Move-Item -Path "<printed cache path>\*" -Destination ".\dataset" -Force
```

(`Move-Item` on the same drive is a fast rename, not a byte-for-byte copy, so this
is quick even though the files are large.)

### Resulting structure

`dataset/` ends up with 18 files: two illicit-ratio variants (`HI` = higher illicit
ratio, `LI` = lower) × three sizes (`Small`, `Medium`, `Large`), each with three
files:

| File | Contents |
|---|---|
| `{PREFIX}-{SIZE}_Trans.csv` | Transaction log with the `Is Laundering` label |
| `{PREFIX}-{SIZE}_accounts.csv` | Account → Bank / Entity mapping |
| `{PREFIX}-{SIZE}_Patterns.txt` | Ground-truth laundering "attempts" (cycles, fan-out, etc.) |

Sizes range from ~34MB (`HI-Small_accounts.csv`) to ~17GB (`HI-Large_Trans.csv`).
`scripts/eda.ipynb` defaults to `HI-Small` (475MB, 5M rows) — change the
`PREFIX`/`SIZE` config cell to analyze a different variant.
