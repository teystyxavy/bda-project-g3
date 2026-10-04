from pathlib import Path

import kagglehub


DATASET = "ealtman2019/ibm-transactions-for-anti-money-laundering-aml"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "dataset"

# Choose one illicit-ratio variant and dataset size. HI-Small is the notebook's
# default and requires the smallest download (about 510MB for the two CSVs).
PREFIX = "HI"
# PREFIX = "LI"

SIZE = "Small"
# SIZE = "Medium"
# SIZE = "Large"

# Pattern descriptions are optional and are not used by the current notebook.
INCLUDE_PATTERNS = True


def selected_files() -> list[str]:
    files = [
        f"{PREFIX}-{SIZE}_Trans.csv",
        f"{PREFIX}-{SIZE}_accounts.csv",
    ]
    if INCLUDE_PATTERNS:
        files.append(f"{PREFIX}-{SIZE}_Patterns.txt")
    return files


def main() -> None:
    """Download only the files for the selected dataset variant."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for filename in selected_files():
        path = kagglehub.dataset_download(
            DATASET,
            path=filename,
            output_dir=str(OUTPUT_DIR),
        )
        print(f"Downloaded {filename} to {path}")


if __name__ == "__main__":
    main()
