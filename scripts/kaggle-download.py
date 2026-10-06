import argparse
import json
from pathlib import Path
from bda_pipeline.acquisition import acquire_dataset


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
    parser = argparse.ArgumentParser(description='Versioned Kaggle acquisition with verified local reuse')
    parser.add_argument('--subset', default='HI-Small')
    parser.add_argument('--dataset-version', type=int)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--output', type=Path, default=OUTPUT_DIR)
    parser.add_argument('--include-patterns', action='store_true')
    args = parser.parse_args()
    record = acquire_dataset(args.output, args.subset, args.dataset_version, args.refresh)
    if args.include_patterns:
        import kagglehub
        version = record['dataset_version']
        handle = DATASET if version is None else f'{DATASET}/versions/{version}'
        kagglehub.dataset_download(handle, path=f'{args.subset}_Patterns.txt', output_dir=str(args.output), force_download=args.refresh)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
