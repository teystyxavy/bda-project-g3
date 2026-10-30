"""Push a Docker OCI archive through ECR APIs using the existing AWS profile.

No registry password is requested or saved. The archive must match the reviewed
image digest. Existing layers are reused; every uploaded blob is hash-checked.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile

import boto3
from botocore.config import Config

ACCOUNT = '588738581042'
REGION = 'ap-southeast-1'
REPOSITORY = 'is459-g3-training'


def push(archive, expected, tag, session):
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', expected):
        raise ValueError('Require reviewed SHA256 image digest')
    sdk = Config(connect_timeout=10, read_timeout=60,
                 retries={'mode': 'standard', 'total_max_attempts': 3})
    if session.client('sts', config=sdk).get_caller_identity()['Account'] != ACCOUNT:
        raise ValueError('Wrong AWS account')
    ecr = session.client('ecr', config=sdk)
    with tarfile.open(archive, 'r') as source:
        def member(digest):
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                raise ValueError('Invalid OCI digest')
            item = source.getmember('blobs/sha256/' + digest.split(':')[1])
            if not item.isfile():
                raise ValueError('Blob must be a regular archive member')
            return item

        def document(digest):
            item = member(digest)
            if item.size > 4_194_304:
                raise ValueError('Oversized OCI manifest')
            data = source.extractfile(item).read()
            if 'sha256:' + hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('OCI manifest hash mismatch')
            return data, json.loads(data)

        def blob(descriptor):
            digest = descriptor['digest']
            item = member(digest)
            if item.size != descriptor['size']:
                raise ValueError('OCI blob size mismatch')
            found = ecr.batch_check_layer_availability(repositoryName=REPOSITORY, layerDigests=[digest])
            if any(layer['layerAvailability'] == 'AVAILABLE' for layer in found['layers']):
                return
            upload = ecr.initiate_layer_upload(repositoryName=REPOSITORY)
            step = min(upload['partSize'], 20 * 1024 * 1024)
            if step < 5 * 1024 * 1024:
                raise ValueError('Unexpected ECR upload part size')
            offset = 0
            checksum = hashlib.sha256()
            with source.extractfile(item) as stream:
                while data := stream.read(step):
                    checksum.update(data)
                    ecr.upload_layer_part(repositoryName=REPOSITORY, uploadId=upload['uploadId'],
                                          partFirstByte=offset, partLastByte=offset + len(data) - 1,
                                          layerPartBlob=data)
                    offset += len(data)
            if 'sha256:' + checksum.hexdigest() != digest:
                raise ValueError('OCI blob hash mismatch')
            ecr.complete_layer_upload(repositoryName=REPOSITORY, uploadId=upload['uploadId'], layerDigests=[digest])
            print('Uploaded verified blob', digest, flush=True)

        def manifest(digest, image_tag=None):
            data, value = document(digest)
            if 'manifests' in value:
                for child in value['manifests']:
                    manifest(child['digest'])
            else:
                for descriptor in [value['config'], *value['layers']]:
                    blob(descriptor)
            try:
                ecr.put_image(repositoryName=REPOSITORY, imageManifest=data.decode(),
                              imageManifestMediaType=value['mediaType'], imageDigest=digest,
                              **({'imageTag': image_tag} if image_tag else {}))
            except ecr.exceptions.ImageAlreadyExistsException:
                pass

        index = json.load(source.extractfile('index.json'))
        if len(index['manifests']) != 1 or index['manifests'][0]['digest'] != expected:
            raise ValueError('Archive does not match the reviewed image')
        manifest(expected, tag)
    remote = ecr.describe_images(repositoryName=REPOSITORY, imageIds=[{'imageTag': tag}])
    if remote['imageDetails'][0]['imageDigest'] != expected:
        raise ValueError('Remote ECR image differs from reviewed image')
    print('Verified ECR image:', expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--digest', required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--profile', default='is459-g3-tools')
    args = parser.parse_args()
    push(args.archive, args.digest, args.tag,
         boto3.Session(profile_name=args.profile, region_name=REGION))


if __name__ == '__main__':
    main()
