"""Build the public dashboard status Lambda zip locally. No cloud calls or secrets.

Packages dashboard_status.py and dashboard_api.py (shared stage mapping) from
src/bda_pipeline into dashboard-status.zip, with a SHA-256 manifest beside it.
Requires a fresh --output directory. This builds an artifact only; deploying
infra/dashboard-status is a separate operation. The public handler reports
workflow status and has no run-start or cancellation route."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from bda_pipeline.common.packaging import module_file

MODULES = ['bda_pipeline.dashboard_status', 'bda_pipeline.dashboard_api']


def flat_name(module):
    """``'bda_pipeline.x.dashboard_status'`` -> ``'dashboard_status.py'`` (zip root)."""
    return module.rsplit('.', 1)[-1] + '.py'


def prepare(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    output.mkdir(parents=True)
    archive = output / 'dashboard-status.zip'
    # The status handler reuses the control API's stage mapping; only these two
    # modules ship, flat at the zip root (handler ``dashboard_status.handler``).
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as package:
        for module in MODULES:
            package.write(module_file(module), flat_name(module))
    manifest = {'schema_version': 1, 'status': 'prepared-not-deployed',
                'file': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'members': [flat_name(module) for module in MODULES]}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    result = prepare(parser.parse_args().output)
    print(f"Prepared {result['file']}; no AWS resources created")


if __name__ == '__main__':
    main()
