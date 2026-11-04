"""Build the streaming dashboard publisher Lambda zip locally. No cloud calls or secrets.

Packages dashboard_streaming.py from src/bda_pipeline into
dashboard-streaming.zip, with a SHA-256 manifest beside it. The Lambda runtime
already provides boto3, so no dependencies ship. Requires a fresh --output
directory. This builds an artifact only; deploying the publisher in
infra/streaming is a separate operation. The publisher reads committed scores
and writes one dashboard snapshot; it cannot score, replay or change state."""
import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

MEMBERS = ['dashboard_streaming.py']


def prepare(output):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    root = Path(__file__).resolve().parents[2]
    output.mkdir(parents=True)
    archive = output / 'dashboard-streaming.zip'
    with ZipFile(archive, 'w', compression=ZIP_DEFLATED) as package:
        for member in MEMBERS:
            package.write(root / 'src/bda_pipeline' / member, member)
    manifest = {'schema_version': 1, 'status': 'prepared-not-deployed',
                'file': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                'members': MEMBERS}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    result = prepare(parser.parse_args().output)
    print(f"Prepared {result['file']}; no AWS resources created")


if __name__ == '__main__':
    main()
