"""Bundle the pinned Linux runtime wheels for offline Glue installation."""
import argparse
import email
import hashlib
import json
from pathlib import Path
import re
import zipfile


def bundle(wheels, output, requirements):
    output = Path(output)
    manifest_path = output.with_suffix('.manifest.json')
    if output.exists() or manifest_path.exists() or not output.name.endswith('.gluewheels.zip'):
        raise ValueError('Use a fresh .gluewheels.zip artifact')
    normalize = lambda name: re.sub(r'[-_.]+', '-', name.lower())
    expected = {}
    for line in Path(requirements).read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            name, version = line.split('==')
            expected[normalize(name)] = version
    found, files = {}, []
    for path in sorted(Path(wheels).glob('*.whl')):
        if path.is_symlink() or 'win_' in path.name or 'aarch64' in path.name:
            raise ValueError('Expected Linux x86_64 or pure Python wheels')
        with zipfile.ZipFile(path) as archive:
            metadata_files = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
            if len(metadata_files) != 1:
                raise ValueError('Invalid wheel metadata')
            metadata = email.message_from_bytes(archive.read(metadata_files[0]))
        name = normalize(metadata['Name'])
        if name in found:
            raise ValueError('Duplicate wheel distribution')
        found[name] = metadata['Version']
        files.append(dict(file=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          bytes=path.stat().st_size))
    if found != expected:
        raise ValueError('Wheel distributions differ from pinned requirements')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as archive:
        for item in files:
            archive.write(Path(wheels) / item['file'], item['file'])
    manifest = dict(files=files, versions=found,
                    sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                    bytes=output.stat().st_size, status='bundled-not-uploaded')
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheels', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = bundle(args.wheels, args.output,
                    Path(__file__).resolve().parents[1] / 'jobs/scoring-requirements.txt')
    print(f'Bundled {len(result["files"])} runtime wheels, {result["bytes"]:,} bytes; no upload')
