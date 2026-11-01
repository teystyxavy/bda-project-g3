"""Prepare an offline, allowlisted dashboard release. Never upload or deploy.

Where this sits in the pipeline
-------------------------------
The public dashboard is four static files served from S3 via CloudFront
under the ``dashboard/`` key prefix. Before those files can be published,
this module packages a candidate release and checks that it is safe to show:

* only the four allowlisted runtime files are copied (nothing else from the
  build folder can leak onto the website);
* the snapshot must be a complete, full-population build with frozen-model
  provenance;
* no credential-like strings, private file paths, or row-level laundering
  labels inside a review queue may appear.

These are defensive pattern and field checks, not a comprehensive secret scanner.
Passing them does not establish that arbitrary content is safe to publish.

It writes ``<output>/site/<four files>`` plus ``<output>/manifest.json``
(sizes, content types, S3 keys and SHA-256 per file). The manifest is kept
outside ``site/`` so it is not itself served.

Callers:
    * ``ingest_compute`` at the end of the Glue baseline/export run;
    * ``scripts/dashboard/prepare-dashboard-release.py`` locally;
    * ``ingest_control`` (Lambda control plane) imports ``ASSETS``,
      ``_inspect`` and ``SENSITIVE_TEXT`` to re-check a release before it
      updates the live dashboard pointer.
"""
import hashlib
import json
import re
from pathlib import Path


# Allowlist: the only files a release may contain, with the HTTP
# Content-Type each should be served with from S3.
ASSETS = {
    'index.html': 'text/html; charset=utf-8',
    'dashboard.css': 'text/css; charset=utf-8',
    'dashboard.js': 'application/javascript; charset=utf-8',
    'dashboard.json': 'application/json; charset=utf-8',
}
# Field names (normalised by _key) that would reveal the ground-truth label
# for an individual transaction. They are forbidden inside any "queue" so a
# public viewer cannot see which listed rows are truly laundering.
LABELS = {'islaundering', 'groundtruth', 'attemptid', 'patterntype'}
# Field names that look like secrets; forbidden anywhere in the snapshot.
SECRETS = {'awsaccesskeyid', 'awssecretaccesskey', 'awssessiontoken',
           'password', 'privatekey', 'credentials', 'apikey', 'accesstoken'}
# Text patterns that must never be published: AWS access key IDs (long-term
# AKIA... or temporary ASIA...), PEM private key headers, and local user
# folder paths (Windows C:\Users\..., macOS /Users/..., Linux /home/...).
SENSITIVE_TEXT = re.compile(
    r'(?:AKIA|ASIA)[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'
    r'|[A-Za-z]:[\\/]Users[\\/]|/Users/|/home/', re.I)


def _key(value):
    """Normalise a JSON key for comparison: lowercase, letters/digits only.

    So ``is_laundering``, ``IsLaundering`` and ``is-laundering`` all become
    ``islaundering`` and are caught by the same rule.
    """
    return re.sub('[^a-z0-9]', '', value.lower())


def _inspect(value, in_queue=False):
    """Recursively walk a JSON value and reject unsafe content.

    Args:
        value: any parsed JSON value (dict, list, str, number, ...).
        in_queue: True once we are inside a ``queue`` key; label fields are
            only forbidden there (aggregate metrics such as precision are
            computed from labels and are allowed elsewhere).

    Raises:
        ValueError: on a secret-like key anywhere, a label key inside a
        queue, or a string matching ``SENSITIVE_TEXT``.
    """
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

    Args:
        source: folder containing the built dashboard (the four ASSETS).
        output: new folder for the release; must not exist and must not be
            the source folder or inside it.

    Returns:
        The manifest dict (also written to ``output/manifest.json``) with
        ``status='prepared-not-published'`` and ``public_demo_approved=False``:
        publishing is a separate, explicit step.

    Raises:
        ValueError: on a missing, symlinked or oversized (>10 MB) file, an
        incomplete or non-full snapshot, missing model provenance, sensitive
        content, or a total package over 12 MB.
    """
    # resolve() turns relative paths and '..' into absolute paths so the
    # "output inside source" check below cannot be bypassed.
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or output == source or source in output.parents:
        raise ValueError('Use a fresh release directory outside the source directory')
    blobs = {}
    for name in ASSETS:
        path = source / name
        # Symlinks are refused because they could point at an arbitrary file.
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 10_000_000:
            raise ValueError(f'Missing, symlinked or oversized asset: {name}')
        blobs[name] = path.read_bytes()
    snapshot = json.loads(blobs['dashboard.json'])
    if snapshot.get('buildStatus') != 'complete' or snapshot.get('aml_scope') != 'full':
        raise ValueError('Release requires a complete full-population dashboard')
    if not snapshot.get('model', {}).get('provenance', {}).get('model_sha256'):
        raise ValueError('Release requires frozen-model provenance')
    # Hash of the original (pre-sanitised) snapshot, kept for traceability.
    source_hash = hashlib.sha256(blobs['dashboard.json']).hexdigest()
    # The curated path is internal (it can be a local folder path), so drop it
    # from the public copy. Only the in-memory copy changes, not the source.
    snapshot.get('provenance', {}).pop('curated', None)
    _inspect(snapshot)
    blobs['dashboard.json'] = json.dumps(snapshot, allow_nan=False).encode('utf-8')
    # Second, plain-text scan over every file (HTML/CSS/JS too).
    for name, blob in blobs.items():
        if SENSITIVE_TEXT.search(blob.decode('utf-8')):
            raise ValueError(f'Credential-like value or private path in {name}')
    total = sum(map(len, blobs.values()))
    # Keeps page load reasonable and stops an accidental full-data export.
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
