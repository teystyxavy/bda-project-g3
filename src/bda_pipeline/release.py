"""Prepare an offline, allowlisted dashboard release. Never upload or deploy."""
import hashlib
import json
import re
from pathlib import Path


ASSETS = {
    'index.html': 'text/html; charset=utf-8',
    'dashboard.css': 'text/css; charset=utf-8',
    'dashboard.js': 'application/javascript; charset=utf-8',
    'dashboard.json': 'application/json; charset=utf-8',
}
LABELS = {'islaundering', 'groundtruth', 'attemptid', 'patterntype'}
SECRETS = {'awsaccesskeyid', 'awssecretaccesskey', 'awssessiontoken',
           'password', 'privatekey', 'credentials', 'apikey', 'accesstoken'}
SENSITIVE_TEXT = re.compile(
    r'(?:AKIA|ASIA)[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
    r'|[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/', re.I)


def _key(value):
    return re.sub('[^a-z0-9]', '', value.lower())


def _inspect(value, in_queue=False):
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = _key(key)
            if normalized in SECRETS or (in_queue and normalized in LABELS):
                raise ValueError('Forbidden credential or queue-label field')
            _inspect(child, in_queue or key == 'queue')
    elif isinstance(value, list):
        for child in value:
            _inspect(child, in_queue)
    elif isinstance(value, str) and SENSITIVE_TEXT.search(value):
        raise ValueError('Credential-like value or private filesystem path')


def prepare_release(source, output):
    """Copy only four runtime assets; keep the manifest outside the served root.

    This is a defensive packaging check, not a general secret scanner or approval
    to publish synthetic identifiers. Evaluation metrics retain aggregate labels;
    row-level queue labels are rejected. Source files are never modified.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output == source or source in output.parents:
        raise ValueError('Use a fresh release directory outside the source directory')
    blobs = {}
    for name in ASSETS:
        path = source / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 10_000_000:
            raise ValueError(f'Missing, symlinked or oversized asset: {name}')
        blobs[name] = path.read_bytes()
    snapshot = json.loads(blobs['dashboard.json'])
    if snapshot.get('buildStatus') != 'complete' or snapshot.get('aml_scope') != 'full':
        raise ValueError('Release requires a complete full-population dashboard')
    if not snapshot.get('model', {}).get('provenance', {}).get('model_sha256'):
        raise ValueError('Release requires frozen-model provenance')
    source_hash = hashlib.sha256(blobs['dashboard.json']).hexdigest()
    snapshot.get('provenance', {}).pop('curated', None)
    _inspect(snapshot)
    blobs['dashboard.json'] = json.dumps(snapshot, allow_nan=False).encode('utf-8')
    for name, blob in blobs.items():
        if SENSITIVE_TEXT.search(blob.decode('utf-8')):
            raise ValueError(f'Credential-like value or private path in {name}')
    total = sum(map(len, blobs.values()))
    if total > 12_000_000:
        raise ValueError('Release exceeds the 12 MB demo package guard')
    manifest = dict(schema_version=1, status='prepared-not-published',
                    public_demo_approved=False, source_snapshot_sha256=source_hash,
                    total_bytes=total, files=[dict(file=name, key=f'dashboard/{name}',
                        content_type=ASSETS[name], bytes=len(blob),
                        sha256=hashlib.sha256(blob).hexdigest())
                        for name, blob in blobs.items()])
    (output / 'site').mkdir(parents=True)
    for name, blob in blobs.items():
        (output / 'site' / name).write_bytes(blob)
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest
