"""Portable frozen-model inference and offline packaging; no AWS SDK calls."""
import json
import shutil
import zipfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .model import CATEGORICAL, SavedRanker, digest
from .batch import FEATURES

IDENTIFIERS = ['transaction_id', 'from_bank', 'from_account', 'to_bank', 'to_account']
SCORING_COLUMNS = ['transaction_id', 'source_row_index', 'timestamp', 'from_bank',
                   'from_account', 'to_bank', 'to_account', 'amount_usd_asof',
                   'amount_usd', *FEATURES, *CATEGORICAL]


def verify_artifacts(directory, manifest_sha256):
    directory = Path(directory)
    manifest_path = directory / 'model-manifest.json'
    if digest(manifest_path) != manifest_sha256:
        raise ValueError('Trusted manifest hash mismatch')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    selected = manifest['model_file']
    if Path(selected).name != selected or not selected.endswith('.ubj'):
        raise ValueError('Invalid model artifact filename')
    expected_names = {'comparison.json', 'features.json', 'selection.json', selected}
    if set(manifest['files']) != expected_names:
        raise ValueError('Invalid artifact allowlist')
    for name, expected in manifest['files'].items():
        if (directory / name).is_symlink() or digest(directory / name) != expected:
            raise ValueError(f'Artifact manifest mismatch: {name}')
    return manifest


@lru_cache(maxsize=4)
def worker_ranker(directory, manifest_sha256, threads=1):
    if not 1 <= threads <= 4:
        raise ValueError('Use between one and four model threads')
    manifest = verify_artifacts(directory, manifest_sha256)
    ranker = SavedRanker(directory, threads=threads)
    if ranker.selection['selected']['model_file'] != manifest['model_file']:
        raise ValueError('Manifest selected-model mismatch')
    return ranker


def score_frame(frame, ranker):
    missing = set(SCORING_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f'Missing scoring columns: {sorted(missing)}')
    result = frame[SCORING_COLUMNS].copy()
    if result.isna().any().any():
        raise ValueError('Null scoring input')
    for column in IDENTIFIERS + CATEGORICAL:
        if not result[column].map(lambda value: isinstance(value, str) and bool(value)).all():
            raise ValueError(f'Expected nonempty strings: {column}')
    if not pd.api.types.is_datetime64_any_dtype(result.timestamp):
        raise ValueError('Expected datetime timestamp')
    if (not pd.api.types.is_integer_dtype(result.source_row_index)
            or (result.source_row_index < 0).any()
            or result.transaction_id.duplicated().any()
            or result.source_row_index.duplicated().any()):
        raise ValueError('Invalid or duplicate source identity')
    for column in ['amount_usd', 'amount_usd_asof', *FEATURES]:
        values = result[column].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f'Invalid nonnegative numeric feature: {column}')
    scores = ranker.predict(result)
    if len(scores) != len(result) or not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
        raise ValueError('Invalid ranking scores')
    result['model_score'] = scores.astype(np.float64)
    return result


def prepare_inference(model_dir, scored, output, package_dir):
    """Bundle source/model plus a small native reference; never retrain."""
    import pyarrow.dataset as ds
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh inference package directory')
    model_dir = Path(model_dir)
    ranker = SavedRanker(model_dir, threads=1)
    if digest(scored) != ranker.selection['source_sha256']:
        raise ValueError('Scored source hash mismatch')
    name = ranker.selection['selected']['model_file']
    files = ['comparison.json', 'features.json', 'selection.json', name]
    manifest = dict(schema_version=1, model_file=name,
                    files={name: digest(model_dir / name) for name in files})
    dataset = ds.dataset(scored, format='parquet')
    cohorts = []
    for date in ['2022-09-01', '2022-09-07', '2022-09-09', '2022-09-12']:
        cohorts.append(dataset.scanner(columns=SCORING_COLUMNS,
            filter=(ds.field('timestamp') >= pd.Timestamp(date)) &
                   (ds.field('timestamp') < pd.Timestamp(date) + pd.Timedelta(days=1)))
                       .head(256).to_pandas())
    cohort = pd.concat(cohorts, ignore_index=True)
    # Spark/Glue expects microsecond Parquet timestamps, not pandas' nanoseconds.
    # Source timestamps have minute precision, so this loses no source information.
    cohort['timestamp'] = cohort.timestamp.dt.as_unit('us')
    expected = score_frame(cohort, ranker)
    artifacts = output / 'artifacts'
    artifacts.mkdir(parents=True)
    for name in files:
        shutil.copyfile(model_dir / name, artifacts / name)
    (artifacts / 'model-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    # Pure Python source only. Native dependencies are a separate Linux artifact.
    with zipfile.ZipFile(output / 'bda_pipeline.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(package_dir).glob('*.py')):
            info = zipfile.ZipInfo('bda_pipeline/' + path.name, date_time=(2022, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    (output / 'fixture').mkdir()
    cohort.to_parquet(output / 'fixture/input.parquet', index=False)
    expected.to_parquet(output / 'fixture/expected.parquet', index=False)
    queue = ranker.queue(expected, expected.model_score.to_numpy())
    queue.to_parquet(output / 'fixture/expected-queue.parquet', index=False)
    metadata = dict(manifest_sha256=digest(artifacts / 'model-manifest.json'),
                    code_sha256=digest(output / 'bda_pipeline.zip'),
                    cohort_rows=len(cohort), source_sha256=digest(scored),
                    status='local-package-not-deployed')
    (output / 'package.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata
