"""Create an allowlisted Lambda image build context; no cloud writes.

Where this sits in the pipeline
-------------------------------
In the default "cloud acquisition" route, the Kaggle download and FX fetch run
inside an AWS Lambda function (``is459-g3-acquire``) packaged as a container
image. This script assembles the *Docker build context* for that image in a
fresh local folder, copying only an explicit allowlist of files so that no
dataset, credentials, Terraform state or unrelated workspace files can end up
inside the image:

* ``cloud-ingestion/Dockerfile`` and ``cloud-ingestion/requirements.txt``;
* a cut-down ``bda_pipeline`` package with just the acquisition modules
  (``cloud_acquisition``, ``acquisition``, ``dataset_options``,
  ``ingest_contract``, ``fx``, ``transform``) and a minimal ``__init__.py``
  (so the heavy training dependencies are not needed in Lambda).

It then writes ``build-context.json`` mapping every file to its SHA-256.
``deploy-cloud-ingestion.ps1`` re-checks these hashes before deploying, so any
edit after preparation is detected.

Next steps (manual): ``docker build`` this folder, then run
``scripts/aws/deploy-cloud-ingestion.ps1``.

Usage
-----
    python scripts/aws/prepare-cloud-ingestion.py --output output/cloud-acquisition-context-YYYYMMDD
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def prepare(output):
    """Copy the allowlisted files into ``output`` and record their hashes.

    Args:
        output: ``Path`` of a folder that must not exist yet (prevents mixing
            old and new files in one build context).

    Returns:
        None. Prints how many files were prepared.

    Raises:
        ValueError: if ``output`` already exists.
    """
    # Repository root (this file lives in scripts/aws/).
    root = Path(__file__).resolve().parents[2]
    if output.exists(): raise ValueError('Use a fresh output directory')
    output.mkdir(parents=True)
    for name in ['Dockerfile', 'requirements.txt']:
        shutil.copyfile(root / 'cloud-ingestion' / name, output / name)
    package = output / 'bda_pipeline'
    package.mkdir()
    # Replace the package __init__ with a one-line docstring so importing the
    # package does not pull in training-only modules.
    (package / '__init__.py').write_text('"""AWS acquisition package; no training dependencies."""\n')
    for name in ['cloud_acquisition.py', 'acquisition.py', 'dataset_options.py', 'ingest_contract.py', 'fx.py', 'transform.py']:
        shutil.copyfile(root / 'src/bda_pipeline' / name, package / name)
    # Integrity record: relative POSIX path -> SHA-256 for every file copied.
    files = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(output.rglob('*')) if p.is_file()}
    (output / 'build-context.json').write_text(json.dumps(files, indent=2), encoding='utf-8')
    print(f'Prepared {len(files)} allowlisted files; no datasets, credentials or cloud calls')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output)
