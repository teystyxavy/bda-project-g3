"""Bundle the pinned Linux runtime wheels for offline Glue installation.

Where this sits in the pipeline
-------------------------------
The Glue PySpark scoring job (``jobs/score_transactions.py``) needs extra
Python libraries (e.g. XGBoost and its dependencies) that Glue 5.0 does not
ship. Glue can install them with ``--additional-python-modules``, but the
project runs Glue with ``--python-modules-installer-option --no-index`` so the
job never downloads from PyPI at run time (reproducible, and no internet
dependency). Instead, a pre-built ``.gluewheels.zip`` of exact wheel files is
uploaded with the deployment artifacts (see ``prepare-ingestion.py`` and
``infra/ingestion``) and pointed to by that Glue argument.

What it does
------------
1. Reads the pinned ``name==version`` list from ``jobs/scoring-requirements.txt``.
2. Opens every ``*.whl`` in ``--wheels``, rejects Windows/ARM wheels (Glue
   workers are Linux x86_64) and reads the real name/version from each
   wheel's ``*.dist-info/METADATA``.
3. Requires the set of wheels to equal the pinned requirements exactly
   (nothing missing, nothing extra, no duplicates).
4. Writes an uncompressed zip of the wheels plus a ``.manifest.json`` with
   per-wheel SHA-256 hashes and the zip's own hash, for later verification.

No AWS calls are made; uploading happens later through Terraform.

Usage
-----
    python scripts/aws/bundle-scoring-wheels.py --wheels output/wheels \
        --output output/runtime.gluewheels.zip
"""
import argparse
import email
import hashlib
import json
from pathlib import Path
import re
import zipfile


def bundle(wheels, output, requirements):
    """Validate wheel files against pinned requirements and zip them for Glue.

    Args:
        wheels: folder containing the downloaded ``*.whl`` files.
        output: new zip path; must end in ``.gluewheels.zip`` and not exist yet.
        requirements: text file of ``name==version`` lines (``#`` comments ok).

    Returns:
        The manifest dict (files with sha256/bytes, versions, zip sha256/bytes,
        ``status='bundled-not-uploaded'``), also saved next to the zip as
        ``<name>.gluewheels.manifest.json``.

    Raises:
        ValueError: reused output path, wrong platform wheel, symlinked wheel,
            malformed metadata, duplicate distribution, or a mismatch between
            the wheels found and the pinned requirements.
    """
    output = Path(output)
    # with_suffix replaces only the final '.zip', giving
    # 'x.gluewheels.manifest.json'. Refuse to overwrite earlier artifacts.
    manifest_path = output.with_suffix('.manifest.json')
    if output.exists() or manifest_path.exists() or not output.name.endswith('.gluewheels.zip'):
        raise ValueError('Use a fresh .gluewheels.zip artifact')
    # PEP 503 name normalisation: 'Scikit_Learn' and 'scikit-learn' are the
    # same distribution, so compare names in lower-case with '-' separators.
    normalize = lambda name: re.sub(r'[-_.]+', '-', name.lower())
    expected = {}
    for line in Path(requirements).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            name, version = line.split('==')
            expected[normalize(name)] = version
    # found: normalised name -> version read from the wheels themselves.
    # files: what goes in the manifest (file name, SHA-256, size).
    found, files = {}, []
    for path in sorted(Path(wheels).glob('*.whl')):
        # Wheel file names encode the platform tag; Glue runs on Linux x86_64,
        # so Windows (win_amd64) or ARM (aarch64) builds would fail to import.
        if path.is_symlink() or 'win_' in path.name or 'aarch64' in path.name:
            raise ValueError('Expected Linux x86_64 or pure Python wheels')
        with zipfile.ZipFile(path) as archive:
            metadata_files = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
            if len(metadata_files) != 1:
                raise ValueError('Invalid wheel metadata')
            # METADATA uses e-mail header format ("Name: ...", "Version: ..."),
            # so the stdlib email parser can read it. Trusting METADATA rather
            # than the file name catches renamed/mislabelled wheels.
            metadata = email.message_from_bytes(archive.read(metadata_files[0]))
        name = normalize(metadata['Name'])
        if name in found:
            raise ValueError('Duplicate wheel distribution')
        found[name] = metadata['Version']
        files.append(dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          bytes=path.stat().st_size))
    # Exact equality: every pinned package present at its pinned version and
    # no unpinned extras that could change runtime behaviour.
    if found != expected:
        raise ValueError('Wheel distributions differ from pinned requirements')
    output.parent.mkdir(parents=True, exist_ok=True)
    # ZIP_STORED (no compression): wheels are already compressed zips.
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as archive:
        for item in files:
            archive.write(Path(wheels) / item['file'], item['file'])
    manifest = dict(files=files, versions=found,
                    sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                    bytes=output.stat().st_size, status='bundled-not-uploaded')
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


# CLI: the requirements file location is fixed relative to the repo root.
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = bundle(args.wheels, args.output,
                    Path(__file__).resolve().parents[2] / 'jobs/scoring-requirements.txt')
    print(f'Bundled {len(result["files"])} runtime wheels, {result["bytes"]:,} bytes; no upload')
