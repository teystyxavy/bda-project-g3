"""Prepare a small training build context; excludes data, credentials and Terraform state.

Where this sits in the pipeline
-------------------------------
The SageMaker steps of the batch workflow (XGBoost training, and the
"baseline" and "export" processing jobs that score, collect evidence and build
the dashboard release) all run one custom Docker image,
``is459-g3-training``, stored in ECR. This script assembles that image's
Docker build context in a fresh folder from an explicit allowlist:

* ``sagemaker/Dockerfile``, ``requirements.txt``, ``train.py`` (training entry
  point) and ``pipeline_worker.py`` (processing entry point, called as
  ``python /app/pipeline_worker.py <stage>``);
* the whole ``src/bda_pipeline`` package, subfolders included (without
  ``__pycache__`` or compatibility stubs);
* the ``dashboard/`` static site files, which the export stage publishes.

It writes ``build-context.json`` with each file's SHA-256 so the exact image
contents can be reviewed. The image is then built with Docker and pushed by
``deploy-ingestion.ps1`` / ``update-ingestion.ps1``. No AWS calls here.

Usage
-----
    python scripts/aws/prepare-sagemaker-container.py --output output/training-context
"""
import argparse
import json
import shutil
from pathlib import Path

from bda_pipeline.common.packaging import copy_package, package_sources
from bda_pipeline.common.integrity import digest


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
    for name in ['Dockerfile', 'requirements.txt', 'train.py', 'pipeline_worker.py']:
        shutil.copyfile(root / 'sagemaker' / name, args.output / name)
    # Every module, subfolders included; caches and compatibility stubs excluded.
    copy_package(args.output / 'bda_pipeline', package_sources())
    shutil.copytree(root / 'dashboard', args.output / 'dashboard')
    # digest() is bda_pipeline.common.integrity's SHA-256 helper.
    files = {str(path.relative_to(args.output)): digest(path)
             for path in sorted(args.output.rglob('*')) if path.is_file()}
    (args.output / 'build-context.json').write_text(json.dumps(files, indent=2), encoding='utf-8')
    print(f'Prepared {len(files)} files; no image build, push or AWS calls')


if __name__ == '__main__':
    main()
