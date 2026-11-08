"""Private, identity-bound Parquet checkpoints; never release evidence by themselves."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from .ingest_contract import BUCKET, sha

# Exact previous implementation, tested for identical detection results. This
# lets the hardened export consume the running fallback's existing checkpoints.
COMPATIBLE_NETWORK_HASH = '678861b5e7689285ca8937dd823500b186a3583bc2ec71b18d8122134e369492'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class NetworkCheckpoint:
    TABLES = {
        'links': {'links', 'counts'},
        'payments': {'seen'},
        'result': {'totals', 'candidates', 'pairs', 'loops', 'ring_members', 'ring_edges', 'accounts', 'ring_payments'},
    }

    def __init__(self, s3, run_id, identity, directory, compatible_network_hashes=()):
        self.s3, self.identity = s3, identity
        # Existing Glue role permits private scratch writes here; no IAM change.
        self.prefix = f'tmp/ingest/export-checkpoints/{run_id}/{sha(identity)}/'
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.compatible_network_hashes = compatible_network_hashes

    def load(self, phase):
        key = self.prefix + phase + '/manifest.json'
        # Check existence without treating authorization/network errors as misses.
        found = self.s3.list_objects_v2(Bucket=BUCKET, Prefix=key, MaxKeys=1).get('Contents', [])
        if not any(item['Key'] == key for item in found):
            # Explicit migration only: old results bind the same source and
            # manifest, but an independently tested earlier implementation.
            # The caller pins the exact compatible code hash; no arbitrary
            # checkpoint discovery or weakening of identity/hash checks.
            if phase in ('links', 'result'):
                for code_hash in self.compatible_network_hashes:
                    if code_hash == self.identity.get('network_code_sha256'):
                        continue
                    legacy_identity = dict(self.identity, network_code_sha256=code_hash)
                    legacy = NetworkCheckpoint(self.s3, self.run_id, legacy_identity, self.directory)
                    value = legacy.load(phase)
                    if value is not None:
                        print(f'EXPORT_CHECKPOINT compatible_restore={phase} code={code_hash}', flush=True)
                        return value
            return None
        response = self.s3.get_object(Bucket=BUCKET, Key=key)
        with response['Body'] as body:
            value = json.loads(body.read())
        if value['identity'] != self.identity or set(value['files']) != self.TABLES[phase]:
            raise ValueError('Network checkpoint identity or table mismatch')
        result = {}
        for name, expected in value['files'].items():
            path = self.directory / f'{phase}-{name}.parquet'
            self.s3.download_file(BUCKET, self.prefix + phase + '/' + name + '.parquet', str(path))
            if digest(path) != expected:
                raise ValueError('Network checkpoint checksum mismatch')
            result[name] = pd.read_parquet(path)
            path.unlink()
        print(f'EXPORT_CHECKPOINT restored={phase}', flush=True)
        return result

    def save(self, phase, frames):
        if set(frames) != self.TABLES[phase]:
            raise ValueError('Unexpected network checkpoint tables')
        files = {}
        print(f'EXPORT_CHECKPOINT saving={phase}', flush=True)
        for name, frame in frames.items():
            path = self.directory / f'{phase}-{name}.parquet'
            frame.to_parquet(path, index=True)
            files[name] = digest(path)
            self.s3.upload_file(str(path), BUCKET, self.prefix + phase + '/' + name + '.parquet',
                                ExtraArgs={'ServerSideEncryption': 'AES256'})
            path.unlink()
        # Upload commit metadata last: partial checkpoint uploads are never read.
        self.s3.put_object(Bucket=BUCKET, Key=self.prefix + phase + '/manifest.json',
                           Body=json.dumps({'identity': self.identity, 'files': files}).encode(),
                           ServerSideEncryption='AES256')
        print(f'EXPORT_CHECKPOINT saved={phase}', flush=True)


class ReadOnlyNetworkCheckpoint(NetworkCheckpoint):
    """Reuse committed checkpoints without racing another worker's writes."""

    def save(self, phase, frames):
        print(f'EXPORT_CHECKPOINT write_skipped={phase} read_only=true', flush=True)
