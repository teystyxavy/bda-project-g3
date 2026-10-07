"""Verified local acquisition and historical FX preparation; never starts AWS compute.

What this file does
-------------------
The first step of the pipeline: get the Kaggle IBM AML files, check they are
what we expect, and turn them into the three prepared inputs that a run
uploads to ``s3://<BUCKET>/pipeline/runs/<run_id>/raw/``::

    transactions.csv   the Kaggle transactions (optionally only the first N rows)
    accounts.csv       the Kaggle account metadata (copied unchanged)
    fx.csv             one historical USD rate per currency per day

Who calls it
------------
* ``scripts/ingestion/ingest.py`` / ``kaggle-download.py`` on a laptop
  (``download``, ``prepare`` and ``run --local-acquisition``).
* ``cloud_acquisition.acquire`` inside the ``is459-g3-acquire`` Lambda, which
  is the default path (``ingest.py run``): the same code, but run in AWS.

It only reads/writes local files and calls Kaggle (via ``kagglehub``) and the
public FX APIs (via ``fx.build_daily_rates``). It never talks to S3 or starts
Glue/SageMaker - uploading is the caller's job.

Provenance
----------
For every dataset directory a ``<subset>_download.json`` record stores the
Kaggle dataset version and the SHA-256 of each file, so later runs can prove
they reuse exactly the same bytes, and the run manifest can record which
Kaggle version produced the dashboard.
"""
import csv
import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .fx import build_daily_rates, discover_requirements, write_rates_csv
from .transform import ACCOUNT_COLUMN_MAP
from .dataset_options import resolve_subset, selected_files

# Kaggle dataset handle (owner/slug) used with kagglehub.
DATASET = 'ealtman2019/ibm-transactions-for-anti-money-laundering-aml'
# The exact expected header of <subset>_Trans.csv. Note the two 'Account'
# columns (sender and receiver account) - a duplicate name, which is why the
# header is compared positionally instead of by column name.
HEADER = ['Timestamp', 'From Bank', 'Account', 'To Bank', 'Account', 'Amount Received',
          'Receiving Currency', 'Amount Paid', 'Payment Currency', 'Payment Format', 'Is Laundering']


def digest(path):
    """Return the hex SHA-256 of a file's bytes.

    Streams the file (``hashlib.file_digest``, Python 3.11+) so multi-GB CSVs
    are hashed without loading them into memory. Used for provenance records,
    cache checks and the run manifest.

    Args:
        path: File path (str or Path).

    Returns:
        64-character lowercase hex digest.
    """
    # Acquisition runs without the training libraries in the Lambda image.
    # (model.digest does the same job, but importing model.py would pull in the
    # pyarrow/modelling modules, so this module keeps its own copy.)
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def acquire_dataset(directory, subset='HI-Small', version=None, refresh=False, downloader=None,
                    *, include_patterns=False, files=None):
    """Make sure the selected Kaggle files exist locally and are verified.

    Decision logic:

    * If ``<subset>_download.json`` exists, no ``refresh`` was asked, and the
      requested ``version`` matches (or none was requested), any local file whose
      SHA-256 matches the record is reused - no download.
    * Legacy case: no record, no refresh, no version requested -> reuse whatever
      files are present, but only if *all* of them are present (see below).
    * Otherwise the missing files are downloaded with ``kagglehub``. The Kaggle
      version actually downloaded is read from KaggleHub's cache path
      (``.../versions/<n>/...``) and must equal any pinned version.
    * After that, headers are checked (transactions must match ``HEADER``
      exactly; accounts must contain the columns the curate step needs;
      patterns must be non-empty).
    * Finally the provenance record is (re)written.

    Args:
        directory: Local dataset folder (created if missing).
        subset: Kaggle subset, default ``'HI-Small'``.
        version: Optional Kaggle dataset version to pin (positive int).
        refresh: Force re-download even if verified local copies exist.
        downloader: Injectable replacement for ``kagglehub.dataset_download``
            (tests use a fake; the Lambda passes ``None`` to use the real one).
        include_patterns: Also fetch ``<subset>_Patterns.txt``.
        files: Optional list of file kinds (see ``dataset_options``).

    Returns:
        The provenance record: ``dataset``, ``subset``, ``dataset_version``,
        ``origin`` (``'verified-local-reuse'``, ``'legacy-local-unversioned'``
        or ``'kaggle-api'``) and ``files`` (name -> SHA-256).

    Side effects:
        May download files into ``directory`` and always writes
        ``<subset>_download.json`` there.

    Raises:
        ValueError: bad version, unresolvable/mismatched Kaggle version, or
        unexpected file headers.
    """
    subset = resolve_subset(subset)
    if version is not None and version < 1:
        raise ValueError('Dataset version must be positive')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = selected_files(subset, files, include_patterns)
    metadata = directory / f'{subset}_download.json'
    old = json.loads(metadata.read_text()) if metadata.exists() else None
    # The old record is "compatible" when we are allowed to reuse it and it is
    # for the requested Kaggle version (or no specific version was requested).
    compatible = (not refresh and old is not None and
                  version in [None, old.get('dataset_version')])
    # Files we can keep: present on disk AND byte-identical to the record.
    reusable_names = {name for name in names if compatible and (directory / name).is_file()
                      and digest(directory / name) == old.get('files', {}).get(name)}
    if not old and not refresh and version is None:
        # Keep legacy local reuse, but never mix unversioned files with a new download.
        reusable_names = {name for name in names if (directory / name).is_file()}
    resolved_version = old.get('dataset_version') if compatible else version
    # If we must download something but do not yet know the version, discard
    # all reuse: otherwise unversioned old files could be mixed with files from
    # whatever version Kaggle serves today.
    if len(reusable_names) != len(names) and resolved_version is None:
        reusable_names.clear()
    missing = [name for name in names if name not in reusable_names]
    if missing:
        if downloader is None:
            # Imported lazily so the module works without kagglehub installed
            # when nothing needs downloading.
            from kagglehub import dataset_download
            downloader = dataset_download
        # Without a known version, ask for "latest"; with one, pin it.
        handle = DATASET if resolved_version is None else f'{DATASET}/versions/{resolved_version}'
        pinned_version = resolved_version
        first = Path(downloader(handle, path=missing[0], force_download=refresh))
        # KaggleHub's cache path identifies the resolved dataset version.
        parts = first.parts
        if 'versions' in parts:
            resolved_version = int(parts[parts.index('versions') + 1])
        if resolved_version is None:
            raise ValueError('Cannot resolve downloaded dataset version; specify --dataset-version')
        if pinned_version is not None and resolved_version != pinned_version:
            raise ValueError('Downloaded dataset version differs from requested version')
        # Fetch the remaining files from the *same* resolved version, so all
        # files are consistent even if Kaggle publishes a new version meanwhile.
        for name in missing:
            source = first if name == missing[0] else Path(downloader(
                f'{DATASET}/versions/{resolved_version}', path=name, force_download=refresh))
            # kagglehub may return either the file or its containing folder.
            if source.is_dir():
                source = source / name
            # Copy out of the KaggleHub cache into our dataset folder (unless
            # the cache already *is* that folder).
            if source.resolve() != (directory / name).resolve():
                shutil.copyfile(source, directory / name)
    # Structural checks on every selected file (reused or downloaded).
    for name in names:
        if name.endswith('_Patterns.txt'):
            if not (directory / name).stat().st_size:
                raise ValueError('Empty patterns file')
            continue
        # 'utf-8-sig' strips a possible byte-order mark before the first header.
        with (directory / name).open(encoding='utf-8-sig', newline='') as stream:
            header = next(csv.reader(stream), [])
        if name.endswith('_Trans.csv') and header != HEADER:
            raise ValueError('Unexpected transaction header, including duplicate Account positions')
        if name.endswith('_accounts.csv') and not set(ACCOUNT_COLUMN_MAP).issubset(header):
            raise ValueError('Unexpected account header')
    reusable = not missing
    record = {'dataset': DATASET, 'subset': subset, 'dataset_version': resolved_version,
              'origin': 'verified-local-reuse' if reusable and old else
                        'legacy-local-unversioned' if reusable else 'kaggle-api',
              'files': {name: digest(directory / name) for name in names}}
    # Preserve provenance for other file selections only when their version matches.
    # (e.g. an earlier download of Patterns.txt stays recorded if still the same version.)
    saved_files = old.get('files', {}) if old and old.get('dataset_version') == resolved_version else {}
    metadata.write_text(json.dumps({**record, 'files': {**saved_files, **record['files']}}, indent=2), encoding='utf-8')
    return record


def validate_fx(rates, start, end, currencies, max_age_days=7):
    """Check that an FX table fully and safely covers the transaction window.

    Expected columns: ``txn_date`` (the day the rate applies to), ``currency``,
    ``usd_rate`` (USD per 1 unit), ``rate_date`` (the market observation date
    actually used - weekends/holidays carry forward the last observation) and
    ``source`` (which API supplied it).

    Rules:

    * no missing values, no duplicate (day, currency) pairs;
    * every rate is finite and strictly positive;
    * the observation is not in the future relative to the day (no look-ahead)
      and is at most ``max_age_days`` (default 7) old (no stale rates);
    * every (day, currency) in ``[start, end]`` is present.

    Used both when preparing inputs (here) and again inside AWS by
    ``ingest_compute.verify_curated``, so a corrupted ``fx.csv`` is caught twice.

    Args:
        rates: pandas DataFrame of rates.
        start: First required day (date or date-like).
        end: Last required day (inclusive).
        currencies: Currency names that must be covered (IBM dataset names,
            e.g. ``'US Dollar'``).
        max_age_days: Maximum allowed gap between day and observation date.

    Returns:
        A copy of ``rates`` (unchanged values) if valid.

    Raises:
        ValueError: describing the first failed rule.
    """
    required = {'txn_date', 'currency', 'usd_rate', 'rate_date', 'source'}
    if not required.issubset(rates.columns) or rates[list(required)].isna().any().any():
        raise ValueError('Missing FX provenance/value')
    rates = rates.copy()
    # errors='raise' turns any unparsable date/number into an exception instead of NaN.
    days = pd.to_datetime(rates.txn_date, errors='raise')
    observations = pd.to_datetime(rates.rate_date, errors='raise')
    # How many days old the observation is when applied; negative = future data.
    age = (days - observations).dt.days
    values = pd.to_numeric(rates.usd_rate, errors='raise')
    if (rates.duplicated(['txn_date', 'currency']).any() or not np.isfinite(values).all()
            or (values <= 0).any() or (age < 0).any() or (age > max_age_days).any()):
        raise ValueError('Duplicate, nonpositive, future or stale FX rate')
    # Coverage check: build the full expected grid of (day, currency) pairs
    # and require it to be a subset of what the table provides.
    available = set(zip(days.dt.date, rates.currency))
    expected = {(day.date(), currency) for day in pd.date_range(start, end) for currency in currencies}
    if not expected.issubset(available):
        raise ValueError('Incomplete historical FX coverage')
    return rates


def prepare_inputs(transactions, accounts, output, rows=None, fx_file=None, getter=None):
    """Create the three prepared run inputs in a fresh directory.

    Steps:

    1. Stream the Kaggle transactions CSV row by row into
       ``output/transactions.csv`` (optionally stopping after ``rows`` rows for
       a cheap "smoke" run). Each row must have exactly 11 fields and no
       embedded newlines, so that downstream line-based Spark/pandas readers
       see one record per line.
    2. Copy the accounts CSV unchanged.
    3. Scan the transactions for the date range and payment currencies
       (``fx.discover_requirements``).
    4. Build (or load from ``fx_file``) daily USD rates from the day *before*
       the first transaction to the last day. The extra prior day is needed
       because curation computes both ``amount_usd`` (same-day rate) and
       ``amount_usd_asof`` (previous-day rate, i.e. a rate that was actually
       known at transaction time).
    5. Validate the FX table and write ``output/fx.csv``.

    Args:
        transactions: Path to ``HI-Small_Trans.csv``.
        accounts: Path to ``HI-Small_accounts.csv``.
        output: New directory to create (must not already exist, so old files
            can never leak into a run).
        rows: Optional row limit for smoke tests; ``None`` = full subset.
        fx_file: Optional pre-built FX CSV (offline use); otherwise the public
            FX APIs are queried.
        getter: Optional injectable HTTP JSON getter for ``build_daily_rates``
            (used by tests / the Lambda).

    Returns:
        Summary dict used to build the run manifest: ``rows``, ``start_date``,
        ``end_date``, ``currencies``, ``scope`` (``'smoke-prefix'`` or
        ``'selected-subset'``) and ``files`` (name -> sha256 + bytes).

    Raises:
        ValueError: on an existing output directory, bad header/row shape,
        empty or short input, or invalid FX.
    """
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh input directory')
    output.mkdir(parents=True)
    target = output / 'transactions.csv'
    count = 0
    # newline='' is required by the csv module so quoted fields are handled correctly.
    with Path(transactions).open(encoding='utf-8-sig', newline='') as source, target.open('w', encoding='utf-8', newline='') as dest:
        # strict=True makes the reader raise on malformed quoting instead of guessing.
        reader, writer = csv.reader(source, strict=True), csv.writer(dest, lineterminator='\n')
        header = next(reader)
        if header != HEADER:
            raise ValueError('Unexpected transaction CSV header')
        writer.writerow(header)
        for row in reader:
            if rows is not None and count >= rows:
                break
            if len(row) != 11 or any('\n' in cell or '\r' in cell for cell in row):
                raise ValueError('Transactions must have eleven fields and one record per line')
            writer.writerow(row)
            count += 1
    # A row limit larger than the file would silently give fewer rows; reject it.
    if count == 0 or (rows is not None and count != rows):
        raise ValueError('Empty or incomplete requested input')
    shutil.copyfile(accounts, output / 'accounts.csv')
    start, end, currencies = discover_requirements(target)
    # Fetch from start-1 day so the previous-day ('as of') valuation is covered.
    rates = pd.read_csv(fx_file) if fx_file else build_daily_rates(
        currencies, start - timedelta(days=1), end, **({'getter': getter} if getter else {}))
    validate_fx(rates, start - timedelta(days=1), end, currencies)
    write_rates_csv(rates, output / 'fx.csv')
    # The hashes and sizes go into the run manifest and are re-checked in AWS.
    return {'rows': count, 'start_date': str(start), 'end_date': str(end),
            'currencies': currencies, 'scope': 'smoke-prefix' if rows else 'selected-subset',
            'files': {name: {'sha256': digest(output / name), 'bytes': (output / name).stat().st_size}
                      for name in ['transactions.csv', 'accounts.csv', 'fx.csv']}}
