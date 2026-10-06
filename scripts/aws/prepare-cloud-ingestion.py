"""Create an allowlisted Lambda image build context; no cloud writes."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path


def prepare(output):
    root = Path(__file__).resolve().parents[2]
    if output.exists(): raise ValueError('Use a fresh output directory')
    output.mkdir(parents=True)
    for name in ['Dockerfile', 'requirements.txt']:
        shutil.copyfile(root / 'cloud-ingestion' / name, output / name)
    package = output / 'bda_pipeline'
    package.mkdir()
    (package / '__init__.py').write_text('"""AWS acquisition package; no training dependencies."""\n')
    for name in ['cloud_acquisition.py', 'acquisition.py', 'dataset_options.py', 'ingest_contract.py', 'fx.py', 'transform.py']:
        shutil.copyfile(root / 'src/bda_pipeline' / name, package / name)
    files = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(output.rglob('*')) if p.is_file()}
    (output / 'build-context.json').write_text(json.dumps(files, indent=2), encoding='utf-8')
    print(f'Prepared {len(files)} allowlisted files; no datasets, credentials or cloud calls')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    prepare(parser.parse_args().output)
