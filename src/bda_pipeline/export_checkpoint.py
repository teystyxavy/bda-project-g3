"""Small, identity-bound export substage checkpoints, committed metadata last."""
import gzip
import hashlib
import json
import re

from .ingest_contract import BUCKET, sha


class ExportCheckpoint:
    MAX_BYTES = 64_000_000

    def __init__(self, s3, run_id, identity):
        self.s3, self.identity = s3, identity
        self.prefix = f'tmp/ingest/export-checkpoints/{run_id}/{sha(identity)}/export/'

    def key(self, name):
        if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', name):
            raise ValueError('Unexpected export checkpoint name')
        return self.prefix + name + '/'

    def compute(self, name, function):
        prefix = self.key(name)
        key = prefix + 'manifest.json'
        objects = self.s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1).get('Contents', [])
        if any(item['Key'] == key for item in objects):
            with self.s3.get_object(Bucket=BUCKET, Key=key)['Body'] as stream:
                manifest = json.loads(stream.read())
            if manifest['identity'] != self.identity:
                raise ValueError('Export checkpoint identity mismatch')
            with self.s3.get_object(Bucket=BUCKET, Key=prefix + 'payload.json.gz')['Body'] as stream:
                data = stream.read(self.MAX_BYTES + 1)
            if len(data) > self.MAX_BYTES or hashlib.sha256(data).hexdigest() != manifest['sha256']:
                raise ValueError('Export checkpoint checksum/size mismatch')
            import io
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                raw = stream.read(self.MAX_BYTES + 1)
            if len(raw) > self.MAX_BYTES:
                raise ValueError('Oversized export checkpoint payload')
            value = json.loads(raw)
            print(f'EXPORT_SUBSTAGE restored={name}', flush=True)
            return value
        print(f'EXPORT_SUBSTAGE starting={name}', flush=True)
        value = function()
        raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
        if len(raw) > self.MAX_BYTES:
            raise ValueError('Oversized export checkpoint payload')
        data = gzip.compress(raw, mtime=0)
        self.s3.put_object(Bucket=BUCKET, Key=prefix + 'payload.json.gz', Body=data,
                           ServerSideEncryption='AES256')
        self.s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps({
            'identity': self.identity, 'sha256': hashlib.sha256(data).hexdigest()}).encode(),
            ServerSideEncryption='AES256')
        print(f'EXPORT_SUBSTAGE saved={name}', flush=True)
        return value
