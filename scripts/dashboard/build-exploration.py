"""Enrich a reviewed snapshot with complete-population exploration, without AWS calls.

Reads a published snapshot, baseline-scored Parquet, corridor Parquet, frozen
model artifacts and a verified cloud-score cache. Source/model hashes and row
counts must match before BQ1 capacity comparisons and BQ2 projections are added.
Writes dashboard.json plus the three static assets to a fresh --output folder.
Use prepare-dashboard-release.py next to package these assets for review.
Run from the installed repository; --help lists all six required input options."""
import argparse
import json
import shutil
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

from bda_pipeline.cloud_scores import load_verified_scores
from bda_pipeline.dashboard_exploration import enrich_snapshot
from bda_pipeline.model import SavedRanker, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['snapshot', 'scored', 'corridors', 'model', 'verification', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a fresh output directory')
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8'))
    ranker = SavedRanker(args.model)
    source_hash = digest(args.scored)
    if source_hash != ranker.selection['source_sha256']:
        raise ValueError('Reference differs from frozen model source')
    if snapshot['model']['provenance']['model_sha256'] != ranker.selection['selected']['sha256']:
        raise ValueError('Published model differs from reference')
    scores, proof = load_verified_scores(args.verification, source_hash,
        ranker.selection['selected']['sha256'], snapshot['full_rows'])
    enrich_snapshot(snapshot, ds.dataset(args.scored, format='parquet'), scores, ranker,
                    pd.read_parquet(args.corridors))
    snapshot['exploration']['source_proof'] = {'source_sha256': source_hash,
        'cloud_score_cache_sha256': proof['score_cache']['sha256'], 'corridors_sha256': digest(args.corridors),
        'deployment': 'same verified dataset and frozen model; additional local projections'}
    args.output.mkdir(parents=True)
    (args.output / 'dashboard.json').write_text(json.dumps(snapshot, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    root = Path(__file__).resolve().parents[2]
    for name in ['index.html', 'dashboard.js', 'dashboard.css']:
        shutil.copyfile(root / 'dashboard' / name, args.output / name)
    print('Prepared enhanced dashboard from verified full-population sources; no AWS calls')


if __name__ == '__main__':
    main()
