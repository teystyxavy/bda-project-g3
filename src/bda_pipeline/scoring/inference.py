"""Portable frozen-model inference and offline packaging; no AWS SDK calls.

Purpose
-------
After ``model.compare_models`` (locally or in SageMaker) has produced a frozen
XGBoost model, this module is the single shared code path that *uses* it to
score transactions. The same functions run in three places, which is why the
module has no boto3/AWS calls and only depends on local files:

* the Glue 5 batch scoring job ``jobs/score_transactions.py``, where every
  Spark executor scores pandas batches via ``mapInPandas``;
* the streaming demo Lambda ``streaming/handler.py`` (Kinesis -> Lambda ->
  DynamoDB), which scores individual records;
* local/validation scripts (``scripts/validation/*cloud-scoring*``,
  ``scripts/streaming/*``, ``cloud_scores.py``, ``ingest_compute.py``).

Trust model
-----------
The model directory is shipped to AWS (via S3) together with a
``model-manifest.json`` that lists the SHA-256 of every artifact. The caller
passes the *expected* hash of that manifest (e.g. as a Glue job argument), so
``verify_artifacts`` can prove the files on the worker are exactly the ones
that were reviewed - a modified model or schema is rejected before use.

Packaging
---------
``prepare_inference`` (run by ``scripts/aws/prepare-cloud-scoring.py``) builds
the offline bundle that is later uploaded for Glue: the model artifacts plus
manifest, a zip of this Python package, and a small "parity fixture" of input
rows with locally computed expected scores, so the cloud job's results can be
compared with native results.
"""
import json
import shutil
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from ..common.integrity import digest
from ..modeling.model import CATEGORICAL, SavedRanker
from ..features.behaviour import FEATURES
from ..common.packaging import package_sources, write_package_zip

# Columns that must be non-empty strings (identity of the payment and parties).
IDENTIFIERS = ['transaction_id', 'from_bank', 'from_account', 'to_bank', 'to_account']
# Exact input contract for scoring: identity columns, both USD valuations, the
# behaviour features and the categoricals. Note the label ``is_laundering`` is
# deliberately absent - production scoring never sees labels.
SCORING_COLUMNS = ['transaction_id', 'source_row_index', 'timestamp', 'from_bank',
                   'from_account', 'to_bank', 'to_account', 'amount_usd_asof',
                   'amount_usd', *FEATURES, *CATEGORICAL]


def resolve_model_directory(directory, localized_manifest):
    """Default to Spark's localized files, never assume the Glue working directory.

    In Glue, model files are distributed to workers with Spark's file
    distribution mechanism and land in a temporary folder whose path is only
    known through ``SparkFiles.get(...)``. The scoring job passes ``'.'`` to
    mean "use those localized files"; any other value is taken as a real path.

    Args:
        directory: ``'.'`` or an explicit model directory.
        localized_manifest: Path returned by
            ``SparkFiles.get('model-manifest.json')``.

    Returns:
        Absolute ``Path`` of the directory holding the model artifacts.

    Raises:
        ValueError: if ``'.'`` was given but the localized manifest is missing.
    """
    if str(directory) == '.':
        manifest = Path(localized_manifest)
        if not manifest.is_file():
            raise ValueError('Spark-localized model manifest is missing')
        # The manifest sits next to the other localized artifacts.
        return manifest.resolve().parent
    return Path(directory).resolve()


def verify_artifacts(directory, manifest_sha256):
    """Check every model file against a trusted manifest hash.

    Steps:

    1. The manifest's own SHA-256 must equal ``manifest_sha256`` (which the
       caller obtained from a trusted source, e.g. job arguments).
    2. The selected model filename must be a bare ``*.ubj`` name.
    3. The manifest must list exactly the four expected files - no more, no
       fewer (an "allowlist"), so nothing unexpected is loaded.
    4. No artifact may be a symlink (a symlink could redirect to another
       file), and each file's hash must match the manifest.

    Args:
        directory: Folder with ``model-manifest.json`` and the artifacts.
        manifest_sha256: Expected hex SHA-256 of ``model-manifest.json``.

    Returns:
        The parsed manifest dict (``schema_version``, ``model_file``,
        ``files``).

    Raises:
        ValueError: on any mismatch (message includes expected vs. actual).
    """
    directory = Path(directory)
    manifest_path = directory / 'model-manifest.json'
    if digest(manifest_path) != manifest_sha256:
        raise ValueError('Trusted manifest hash mismatch')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    selected = manifest['model_file']
    # Plain filename only: blocks "../" style path traversal.
    if Path(selected).name != selected or not selected.endswith('.ubj'):
        raise ValueError('Invalid model artifact filename')
    expected_names = {'comparison.json', 'features.json', 'selection.json', selected}
    if set(manifest['files']) != expected_names:
        raise ValueError('Invalid artifact allowlist')
    for name, expected in manifest['files'].items():
        if (directory / name).is_symlink():
            raise ValueError(f'Artifact must not be a symlink: {name}')
        actual = digest(directory / name)
        if actual != expected:
            raise ValueError(f'Artifact manifest mismatch: {name}; expected={expected}; actual={actual}')
    return manifest


# Cache loaded rankers per Python process. Spark calls the scoring function once
# per partition, and Lambda reuses warm containers; caching means the model is
# verified and loaded once per worker instead of once per batch. Arguments must
# be hashable (callers pass the directory as a string).
@lru_cache(maxsize=4)
def worker_ranker(directory, manifest_sha256, threads=1):
    """Verify artifacts and return a cached ``SavedRanker`` for this process.

    Args:
        directory: Model directory (string, for the cache key).
        manifest_sha256: Trusted manifest hash (see ``verify_artifacts``).
        threads: XGBoost threads, limited to 1-4. Glue and Lambda use 1 so
            that parallel Spark tasks / concurrent invocations do not
            oversubscribe the CPU.

    Returns:
        A loaded, verified ``model.SavedRanker``.

    Raises:
        ValueError: on a bad thread count, a failed verification, or if the
            manifest and ``selection.json`` disagree about the chosen model.
    """
    if not 1 <= threads <= 4:
        raise ValueError('Use between one and four model threads')
    manifest = verify_artifacts(directory, manifest_sha256)
    # SavedRanker performs its own second layer of hash checks.
    ranker = SavedRanker(directory, threads=threads)
    if ranker.selection['selected']['model_file'] != manifest['model_file']:
        raise ValueError('Manifest selected-model mismatch')
    return ranker


def score_frame(frame, ranker):
    """Validate one batch of transactions and add a ``model_score`` column.

    This is what runs inside Spark's ``mapInPandas`` and inside the streaming
    Lambda. Input is checked strictly so that bad data fails the job instead
    of silently producing misleading scores.

    Checks: all ``SCORING_COLUMNS`` present and non-null; identifiers and
    categoricals are non-empty strings; ``timestamp`` is a datetime;
    ``source_row_index`` is a non-negative integer; neither
    ``transaction_id`` nor ``source_row_index`` repeats within the batch;
    amounts and behaviour features are finite and non-negative; and the
    returned scores are finite and within [0, 1].

    Args:
        frame: pandas DataFrame with at least ``SCORING_COLUMNS``.
        ranker: A ``SavedRanker`` (usually from ``worker_ranker``).

    Returns:
        A new DataFrame with exactly ``SCORING_COLUMNS`` plus
        ``model_score`` (float64). Extra input columns are dropped.

    Raises:
        ValueError: describing the first failed check.
    """
    missing = set(SCORING_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f'Missing scoring columns: {sorted(missing)}')
    # Keep only the contract columns (also guarantees no label is passed on).
    result = frame[SCORING_COLUMNS].copy()
    if result.isna().any().any():
        raise ValueError('Null scoring input')
    for column in IDENTIFIERS + CATEGORICAL:
        # Must be real Python str and not "" (catches numbers or blanks).
        if not result[column].map(lambda value: isinstance(value, str) and bool(value)).all():
            raise ValueError(f'Expected nonempty strings: {column}')
    if not pd.api.types.is_datetime64_any_dtype(result.timestamp):
        raise ValueError('Expected datetime timestamp')
    # Duplicate identities would mean the same transaction is scored twice.
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
    # Sanity-check the model output too (logistic scores must lie in [0, 1]).
    if len(scores) != len(result) or not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any():
        raise ValueError('Invalid ranking scores')
    # float64 matches the DoubleType column declared in the Spark output schema.
    result['model_score'] = scores.astype(np.float64)
    return result


def prepare_inference(model_dir, scored, output, package_dir):
    """Bundle source/model plus a small native reference; never retrain.

    Produces this layout under ``output`` (which must not exist yet)::

        artifacts/            model files + model-manifest.json (hash list)
        bda_pipeline.zip      pure-Python package for Glue's --extra-py-files
        fixture/input.parquet           up to 4 x 256 sample input rows
        fixture/expected.parquet        their locally computed model scores
        fixture/expected-queue.parquet  their explained review queue
        package.json          hashes + status "local-package-not-deployed"

    The fixture rows come from four days (2022-09-01, 09-07, 09-09 and 09-12),
    one in each chronological split, so the parity test covers every period.
    Running the cloud job on ``fixture/input.parquet`` and comparing with
    ``expected.parquet`` proves Glue scores exactly like the local machine.

    Args:
        model_dir: Frozen model directory from ``compare_models``.
        scored: The ``bq1_scored.parquet`` the model was trained on; its hash
            must match ``selection.json``'s ``source_sha256``.
        output: New output directory.
        package_dir: Source folder of ``bda_pipeline`` (``*.py`` files zipped).

    Returns:
        The ``package.json`` metadata dict. ``manifest_sha256`` from it is the
        trusted hash that the scoring job is later given.

    Side effects:
        Only writes local files; nothing is uploaded or deployed.
    """
    import pyarrow.dataset as ds
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh inference package directory')
    model_dir = Path(model_dir)
    ranker = SavedRanker(model_dir, threads=1)
    # Make sure the fixture is drawn from the same data the model was built on.
    if digest(scored) != ranker.selection['source_sha256']:
        raise ValueError('Scored source hash mismatch')
    name = ranker.selection['selected']['model_file']
    files = ['comparison.json', 'features.json', 'selection.json', name]
    manifest = dict(schema_version=1, model_file=name,
                    files={name: digest(model_dir / name) for name in files})
    dataset = ds.dataset(scored, format='parquet')
    cohorts = []
    # First 256 rows of each sample day (filter pushed down to pyarrow).
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
    # Pure Python source only, subfolders included. Native dependencies are a
    # separate Linux artifact. Files are added in sorted order with a fixed
    # timestamp so the zip bytes (and therefore code_sha256) are identical
    # every time it is rebuilt.
    write_package_zip(output / 'bda_pipeline.zip', package_sources(package_dir),
                      date_time=(2022, 1, 1, 0, 0, 0), root=package_dir)
    (output / 'fixture').mkdir()
    cohort.to_parquet(output / 'fixture/input.parquet', index=False)
    expected.to_parquet(output / 'fixture/expected.parquet', index=False)
    queue = ranker.queue(expected, expected.model_score.to_numpy())
    queue.to_parquet(output / 'fixture/expected-queue.parquet', index=False)
    # Hashes recorded here are what later deployment/verification steps trust.
    metadata = dict(manifest_sha256=digest(artifacts / 'model-manifest.json'),
                    code_sha256=digest(output / 'bda_pipeline.zip'),
                    cohort_rows=len(cohort), source_sha256=digest(scored),
                    status='local-package-not-deployed')
    (output / 'package.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return metadata
