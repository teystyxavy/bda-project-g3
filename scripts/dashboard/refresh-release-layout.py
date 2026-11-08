"""Prepare completed export data with the current dashboard layout, without recomputing it.

Use --source pointing to a completed export's release/site or dashboard folder.
The output is a fresh prepared release for publish-exploration.py. No AWS calls,
training, metrics changes, or writes into the source export are performed.
Delta 1 later periods are hidden by the renderer; date choices follow export splits.
"""
import argparse
import json
import shutil
import tempfile
from pathlib import Path

from bda_pipeline.release import prepare_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source / 'dashboard.json'
    snapshot = json.loads(source.read_bytes())
    if snapshot.get('buildStatus') != 'complete' or not snapshot.get('exploration', {}).get('aml'):
        raise ValueError('Require a completed interactive export, not an in-progress checkpoint')
    if not snapshot.get('splits', {}).get('test') or not snapshot.get('model', {}).get('views', {}).get('test'):
        raise ValueError('Require matching test dates and frozen-model views')
    assets = Path(__file__).resolve().parents[2] / 'dashboard'
    with tempfile.TemporaryDirectory(prefix='dashboard-layout-') as scratch:
        stage = Path(scratch)
        shutil.copyfile(source, stage / 'dashboard.json')
        for name in ['index.html', 'dashboard.css', 'dashboard.js']:
            shutil.copyfile(assets / name, stage / name)
        manifest = prepare_release(stage, args.output)
    print(json.dumps({'status': manifest['status'], 'output': str(args.output),
        'source_run_id': snapshot.get('pipeline', {}).get('run_id'),
        'subset': snapshot.get('pipeline', {}).get('subset'),
        'model_sha256': snapshot['model']['provenance']['model_sha256'],
        'model_or_metrics_changed': False, 'training_started': False}))


if __name__ == '__main__':
    main()
