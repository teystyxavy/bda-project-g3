"""Forget only predecessor immutable uploads, retaining their S3 objects.

The updater uses this before planning a new content-addressed deployment.
It backs up private state first; no AWS object is deleted or modified here.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

BUCKET = 'is459-g3-588738581042-ap-southeast-1'
FILES = {'control.zip', 'bda_pipeline.zip', 'runtime.gluewheels.zip',
         'curate_transactions.py', 'score_transactions.py',
         *{'model/' + name for name in ['comparison.json', 'selection.json',
                                       'features.json', 'model-manifest.json', 'deployment.json']}}


def predecessor_addresses(state, prefix):
    if not re.fullmatch(r'pipeline/deployments/[0-9a-f]{64}', prefix):
        raise ValueError('Invalid immutable deployment prefix')
    addresses = []
    for resource in state.get('resources', []):
        if resource.get('type') != 'aws_s3_object' or resource.get('name') != 'deployment_artifact':
            continue
        if resource.get('module') or resource.get('mode') != 'managed':
            raise ValueError('Only root managed immutable uploads may be retained')
        for instance in resource['instances']:
            name = instance.get('index_key')
            attrs = instance['attributes']
            if (not isinstance(name, str) or
                    (name not in FILES and not re.fullmatch(r'model/[A-Za-z0-9_-]+\.ubj', name))):
                raise ValueError('Unexpected deployment artifact')
            key = attrs.get('key', '')
            if (attrs.get('bucket') != BUCKET or
                    not re.fullmatch(r'pipeline/deployments/[0-9a-f]{64}/' + re.escape(name), key)):
                raise ValueError('Artifact is outside the immutable deployment prefix')
            if key != prefix + '/' + name:
                addresses.append('aws_s3_object.deployment_artifact[' + json.dumps(name) + ']')
    return addresses


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--terraform', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--prefix', required=True)
    args = parser.parse_args()
    data = args.state.read_bytes()
    addresses = predecessor_addresses(json.loads(data), args.prefix)
    if not addresses:
        print('No predecessor artifact ownership to release.')
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = args.state.with_name('pre-retain-artifacts-' + stamp + '.tfstate')
    with backup.open('xb') as target:
        target.write(data)
    subprocess.run([str(args.terraform.resolve()), '-chdir=infra/ingestion',
                    'state', 'rm', *addresses], check=True)
    print(f'Retained {len(addresses)} predecessor S3 objects; private state backup: {backup}')


if __name__ == '__main__':
    main()
