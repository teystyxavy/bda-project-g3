"""Build the dashboard control Lambda zip locally. No cloud calls or secrets.

Copies dashboard_api.py and dashboard_sessions.py into dashboard-api.zip and writes a
SHA-256 manifest beside it. Requires a fresh --output directory. This does not
configure authentication or create resources; infra/dashboard-control defines
the separate operator stack. Its handler supports authenticated workflow
controls, unlike the public read-only status handler."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def prepare(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    root = Path(__file__).resolve().parents[2]
    output.mkdir(parents=True)
    archive = output / 'dashboard-api.zip'
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as package:
        package.write(root / 'src/bda_pipeline/dashboard_api.py', 'dashboard_api.py')
        package.write(root / 'src/bda_pipeline/dashboard_sessions.py', 'dashboard_sessions.py')
    manifest = {'schema_version': 1, 'status': 'prepared-not-deployed',
                'file': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'members': ['dashboard_api.py', 'dashboard_sessions.py']}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    result = prepare(parser.parse_args().output)
    print(f"Prepared {result['file']}; no AWS resources created")


if __name__ == '__main__':
    main()
