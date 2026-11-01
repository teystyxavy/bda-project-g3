"""Prepare the training-only image build context; no AWS calls or datasets."""
import argparse
import json
import shutil
from pathlib import Path

from bda_pipeline.model import digest


def main():
    """Copy the allowlisted files into ``--output`` and write their hashes.

    Raises:
        ValueError: if ``--output`` already exists.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a fresh build directory')
    root = Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True)
    for name in ['Dockerfile', 'requirements.txt', 'train.py']:
        shutil.copyfile(root / 'sagemaker' / name, args.output / name)
    shutil.copytree(root / 'src/bda_pipeline', args.output / 'bda_pipeline',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    # digest() is bda_pipeline.model's SHA-256 helper.
    files = {str(path.relative_to(args.output)): digest(path)
             for path in sorted(args.output.rglob('*')) if path.is_file()}
    (args.output / 'build-context.json').write_text(json.dumps(files, indent=2), encoding='utf-8')
    print(f'Prepared {len(files)} files; no image build, push or AWS calls')


if __name__ == '__main__':
    main()
