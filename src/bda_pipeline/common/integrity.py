"""File integrity helper shared by every stage; standard library only."""
import hashlib
from pathlib import Path


def digest(path):
    """Return the SHA-256 hex digest of a file.

    Used everywhere in the project as a tamper/consistency check: inputs,
    model files and manifests are recorded by hash and re-verified before use.
    """
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()
