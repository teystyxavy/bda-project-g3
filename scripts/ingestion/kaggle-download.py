import argparse
import json
from pathlib import Path
from bda_pipeline.acquisition import acquire_dataset
from bda_pipeline.dataset_options import add_selection_arguments, resolve_subset, selected_files, selection_options


OUTPUT_DIR = Path(__file__).resolve().parents[2] / "dataset"


def main() -> None:
    parser = argparse.ArgumentParser(description='Versioned Kaggle acquisition with verified local reuse')
    add_selection_arguments(parser)
    parser.add_argument('--dataset-version', type=int)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--output', type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if args.list_options:
        print(json.dumps(selection_options(), indent=2))
        return
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
    record = acquire_dataset(args.output, subset, args.dataset_version, args.refresh,
                             include_patterns=args.include_patterns, files=args.files)
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
