"""Download (or verify and reuse) IBM AML files from Kaggle into ``dataset/``.

Where this sits in the pipeline
-------------------------------
The very first, purely local step: getting the Kaggle "IBM Transactions for
Anti Money Laundering (AML)" CSVs onto this computer. It is a thin CLI around
``bda_pipeline.acquisition.acquire_dataset`` (the same function ``ingest.py
download`` uses), so local analysis scripts and offline preparation have the
source data. No AWS calls are made.

Behaviour
---------
* Choose one of six subsets (``HI``/``LI`` illicit ratio x
  ``Small``/``Medium``/``Large``); default HI-Small, the only subset the AWS
  pipeline processes.
* Files with a compatible ``<subset>_download.json`` record are reused when
  their SHA-256 matches. Complete legacy local selections can also be reused
  without a version record and are marked ``legacy-local-unversioned``;
  ``--refresh`` forces a fresh download via KaggleHub.
* The CSV headers are checked so a wrong/corrupt file fails immediately.
* ``--dry-run`` and ``--list-options`` print JSON without downloading.

Writes
------
The selected files plus ``<subset>_download.json`` (version + hashes) into
``--output`` (default ``<repo>/dataset``). Prints the acquisition record.

Usage
-----
    python scripts/ingestion/kaggle-download.py --subset HI-Small
    python scripts/ingestion/kaggle-download.py --variant LI --size Medium --include-patterns
"""
import argparse
import json
from pathlib import Path
from bda_pipeline.acquisition import acquire_dataset
from bda_pipeline.dataset_options import add_selection_arguments, resolve_subset, selected_files, selection_options


# Default download folder: <repo>/dataset (this file is two levels below root).
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "dataset"


def main() -> None:
    """Parse options, validate the subset and download/verify the files.

    Returns:
        None. Prints JSON (options list, dry-run preview or acquisition record).

    Side effects:
        Network download from Kaggle (needs Kaggle credentials for KaggleHub)
        and files written into ``--output`` unless ``--dry-run``/``--list-options``.
    """
    parser = argparse.ArgumentParser(description='Versioned Kaggle acquisition with verified local reuse')
    # Shared --subset/--variant/--size/--files/--include-patterns/--dry-run/
    # --list-options flags (same as ingest.py).
    add_selection_arguments(parser)
    parser.add_argument('--dataset-version', type=int)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--output', type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if args.list_options:
        print(json.dumps(selection_options(), indent=2))
        return
    # Turn --subset or --variant/--size into one validated name like 'HI-Small';
    # parser.error() prints usage and exits with code 2 on bad input.
    try:
        subset = resolve_subset(args.subset, args.variant, args.size)
        if args.dataset_version is not None and args.dataset_version < 1:
            raise ValueError('Dataset version must be positive')
    except ValueError as error:
        parser.error(str(error))
    if args.dry_run:
        print(json.dumps({'action': 'download', 'subset': subset, 'directory': str(args.output.resolve()),
                          'files': selected_files(subset, args.files, args.include_patterns),
                          'dataset_version': args.dataset_version, 'refresh': args.refresh,
                          'dry_run': True, 'starts_aws_workflow': False}, indent=2))
        return
    # Download missing files (or reuse verified ones) and check CSV headers.
    record = acquire_dataset(args.output, subset, args.dataset_version, args.refresh,
                             include_patterns=args.include_patterns, files=args.files)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
