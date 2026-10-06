# Kaggle download options

Run from `bda-project-g3/`. The launcher is now under `scripts/ingestion/`.
Install the local acquisition dependencies once in your Python environment:

```powershell
python -m pip install -e ".[ingest]"
```

The launcher selects `output/ml-venv/Scripts/python.exe` when available;
`-Python python` overrides that selection. Local downloads use your existing
Kaggle authentication when required. Credentials never belong in command arguments.

## Selection

| Option | Accepted values / behaviour |
|---|---|
| `-DownloadOnly` | Local Kaggle download; no FX fetch, uploads, AWS calls or pipeline execution |
| `-Variant` (alias `-Prefix`) | `HI` or `LI`; default `HI` |
| `-Size` | `Small`, `Medium` or `Large`; default `Small` |
| `-Subset` | `HI-Small`, `HI-Medium`, `HI-Large`, `LI-Small`, `LI-Medium`, `LI-Large` |
| `-Files` | One or more of `Transactions`, `Accounts`, `Patterns`; default transactions and accounts |
| `-IncludePatterns` | Add the selected subset's `Patterns.txt` to the file selection |
| `-DatasetDirectory` | Local destination; default ignored `dataset/`, relative to the repository root |
| `-DatasetVersion` | A positive, existing Kaggle version; omitted means verified local reuse or latest on a fresh download |
| `-Refresh` | Force selected files to download again; without a version, resolve latest |
| `-DryRun` | Show selection and execution mode without network calls or writes |
| `-ListOptions` | Show all supported subsets/file types without network calls or writes |

Choose either `-Subset` or `-Variant`/`-Size`. If supplied together, they must agree.
Case is normalized. Different subsets coexist by their original filenames.

```powershell
# Inspect all choices.
.\scripts\ingestion\ingest.ps1 -ListOptions

# HI-Small transactions and accounts, reusing verified local files.
.\scripts\ingestion\ingest.ps1 -DownloadOnly

# LI-Medium with optional pattern descriptions; force a fresh download.
.\scripts\ingestion\ingest.ps1 -DownloadOnly -Variant LI -Size Medium -IncludePatterns -Refresh

# Preview HI-Large before downloading.
.\scripts\ingestion\ingest.ps1 -DownloadOnly -Subset HI-Large -DryRun

# Just the accounts and patterns, from a known Kaggle version.
.\scripts\ingestion\ingest.ps1 -DownloadOnly -Subset LI-Small `
  -Files Accounts,Patterns -DatasetVersion 8 -DatasetDirectory dataset/exploration
```

Version 8 is the version recorded in the current acceptance evidence, not a promise
that every future Kaggle release has identical files. Each download records the
resolved version and SHA-256 hashes in `<subset>_download.json`. Adding patterns
or another file to a versioned selection downloads only missing/changed files and
pins them to that version. Without `-Refresh`, unchanged selected files are reused.
Legacy files without a known version remain unversioned; extending that selection
requires downloading the requested files together to establish a consistent version.
Pattern files are downloaded for exploration; they do not become pipeline labels.

The standalone Python downloader accepts equivalent options:

```powershell
python scripts/ingestion/kaggle-download.py --variant LI --size Medium --include-patterns --refresh
python scripts/ingestion/kaggle-download.py --subset HI-Large --files transactions --dry-run
```

Its destination flag is `--output`; the ingestion Python entry point uses
`download --dataset-directory` instead.

## Pipeline boundary and costs

**Download-only runs incur no AWS charges from this command.** Larger subsets
consume more local disk space and transfer time. KaggleHub keeps a cached source
copy as well as the copy placed in `dataset/`, so allow space for both.

**Pipeline runs still support HI-Small only.** Omitting `-DownloadOnly` runs the
existing AWS workflow. `-Refresh` refreshes Kaggle source acquisition; `-Rerun`
bypasses processing caches. An unchanged refreshed source may still reuse a verified
release unless `-Rerun` is also selected.

`-Rows` remains a pipeline smoke-run limit, not a partial Kaggle download: the
complete source CSV is acquired before preparing the row prefix. `-Train`,
`-Rows`, `-Rerun`, `-PrepareOnly`, `-NoWait` and local FX/deployment inputs cannot
be combined with `-DownloadOnly`.

Extending AWS to Medium/Large requires additional implementation and measurements.
The current acquisition Lambda has 4 GB temporary storage and a 15-minute timeout;
both Glue jobs use two G.1X workers with a 10-minute timeout. Larger inputs may
require a different acquisition approach and larger/longer processing jobs, plus
model and reconciliation validation. Glue charges by compute usage and Athena SQL
by data scanned; retained S3 inputs and outputs add storage charges. See
[AWS Glue pricing](https://aws.amazon.com/glue/pricing/) and
[Athena pricing](https://aws.amazon.com/athena/pricing/).

For the USD 250 course budget, keep HI-Small as the end-to-end demo and use other
subsets for local exploration first. No Medium/Large AWS dollar estimate is claimed
without measured sizes, runtimes and regional service rates. These options have
been checked with small offline fixtures and launcher previews; no new large
download or paid AWS run was needed to test them.
