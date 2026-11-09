"""Copy the reviewed streaming consumer image between ECR repositories, byte for byte.

A new streaming run gets its own ECR repository (its repository policy names one
function), but it can serve the same frozen model with the same consumer code. This
copies the existing image instead of rebuilding it: no Docker, no registry password.
Every blob is SHA-256 checked, and the manifest bytes are written unchanged, so the
digest in the target repository equals the reviewed source digest.

Usage::

    python scripts/streaming/copy-streaming-image.py --source is459-g3-streaming-delta1 \\
        --target is459-g3-streaming-hilarge --digest sha256:<64 hex> [--profile is459-g3-tools]
"""
import argparse
import hashlib
import json
import re
import urllib.request

import boto3
from botocore.config import Config

ACCOUNT = '588738581042'
REGION = 'ap-southeast-1'
CHUNK = 20 * 1024 * 1024


def fetch(ecr, repository, digest):
    """Download one blob through a pre-signed layer URL and check its SHA-256."""
    url = ecr.get_download_url_for_layer(repositoryName=repository, layerDigest=digest)['downloadUrl']
    with urllib.request.urlopen(url, timeout=300) as response:
        data = response.read()
    if 'sha256:' + hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f'Blob {digest} failed its SHA-256 check')
    return data


def upload(ecr, repository, digest, data):
    """Upload one blob in parts unless the target repository already has it."""
    known = ecr.batch_check_layer_availability(repositoryName=repository, layerDigests=[digest])['layers']
    if known and known[0].get('layerAvailability') == 'AVAILABLE':
        return 'reused'
    upload_id = ecr.initiate_layer_upload(repositoryName=repository)['uploadId']
    for start in range(0, len(data), CHUNK):
        part = data[start:start + CHUNK]
        ecr.upload_layer_part(repositoryName=repository, uploadId=upload_id, partFirstByte=start,
                              partLastByte=start + len(part) - 1, layerPartBlob=part)
    ecr.complete_layer_upload(repositoryName=repository, uploadId=upload_id, layerDigests=[digest])
    return 'uploaded'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--digest', required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    args = parser.parse_args()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.digest):
        raise ValueError('Require the reviewed sha256 image digest')
    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    config = Config(connect_timeout=10, read_timeout=120, retries={'mode': 'standard', 'total_max_attempts': 3})
    if session.client('sts', config=config).get_caller_identity()['Account'] != ACCOUNT:
        raise ValueError('Wrong AWS account')
    ecr = session.client('ecr', config=config)
    image = ecr.batch_get_image(repositoryName=args.source, imageIds=[{'imageDigest': args.digest}])['images']
    if len(image) != 1:
        raise ValueError('Source image not found')
    manifest_text = image[0]['imageManifest']
    media_type = image[0].get('imageManifestMediaType')
    if 'sha256:' + hashlib.sha256(manifest_text.encode('utf-8')).hexdigest() != args.digest:
        raise ValueError('Source manifest does not match the reviewed digest')
    manifest = json.loads(manifest_text)
    if 'layers' not in manifest:
        raise ValueError('Only single-platform image manifests are supported')
    report = {}
    for blob in [manifest['config'], *manifest['layers']]:
        report[blob['digest']] = upload(ecr, args.target, blob['digest'], fetch(ecr, args.source, blob['digest']))
    put = {'repositoryName': args.target, 'imageManifest': manifest_text, 'imageDigest': args.digest}
    if media_type:
        put['imageManifestMediaType'] = media_type
    try:
        ecr.put_image(**put)
    except ecr.exceptions.ImageAlreadyExistsException:
        pass
    found = ecr.describe_images(repositoryName=args.target, imageIds=[{'imageDigest': args.digest}])['imageDetails']
    if not found:
        raise ValueError('Copied image is not visible in the target repository')
    print(json.dumps({'status': 'copied', 'target': args.target, 'digest': args.digest,
                      'blobs': len(report), 'uploaded': sum(v == 'uploaded' for v in report.values())}))


if __name__ == '__main__':
    main()
