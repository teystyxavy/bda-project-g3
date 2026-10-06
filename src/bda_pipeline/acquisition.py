"""Verified local acquisition and historical FX preparation; never starts AWS compute."""
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

DATASET = 'ealtman2019/ibm-transactions-for-anti-money-laundering-aml'
HEADER = ['Timestamp', 'From Bank', 'Account', 'To Bank', 'Account', 'Amount Received',
          'Receiving Currency', 'Amount Paid', 'Payment Currency', 'Payment Format', 'Is Laundering']


def digest(path):
    # Acquisition runs without the training libraries in the Lambda image.
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def acquire_dataset(directory, subset='HI-Small', version=None, refresh=False, downloader=None,
                    *, include_patterns=False, files=None):
    subset = resolve_subset(subset)
    if version is not None and version < 1:
        raise ValueError('Dataset version must be positive')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = selected_files(subset, files, include_patterns)
    metadata = directory / f'{subset}_download.json'
    old = json.loads(metadata.read_text()) if metadata.exists() else None
    compatible = (not refresh and old is not None and
                  version in [None, old.get('dataset_version')])
    reusable_names = {name for name in names if compatible and (directory / name).is_file()
                      and digest(directory / name) == old.get('files', {}).get(name)}
    if not old and not refresh and version is None:
        # Keep legacy local reuse, but never mix unversioned files with a new download.
        reusable_names = {name for name in names if (directory / name).is_file()}
    resolved_version = old.get('dataset_version') if compatible else version
    if len(reusable_names) != len(names) and resolved_version is None:
        reusable_names.clear()
    missing = [name for name in names if name not in reusable_names]
    if missing:
        if downloader is None:
            from kagglehub import dataset_download
            downloader = dataset_download
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
        for name in missing:
            source = first if name == missing[0] else Path(downloader(
                f'{DATASET}/versions/{resolved_version}', path=name, force_download=refresh))
            if source.is_dir():
                source = source / name
            if source.resolve() != (directory / name).resolve():
                shutil.copyfile(source, directory / name)
    for name in names:
        if name.endswith('_Patterns.txt'):
            if not (directory / name).stat().st_size:
                raise ValueError('Empty patterns file')
            continue
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
    saved_files = old.get('files', {}) if old and old.get('dataset_version') == resolved_version else {}
    metadata.write_text(json.dumps({**record, 'files': {**saved_files, **record['files']}}, indent=2), encoding='utf-8')
    return record


def validate_fx(rates, start, end, currencies, max_age_days=7):
    required = {'txn_date', 'currency', 'usd_rate', 'rate_date', 'source'}
    if not required.issubset(rates.columns) or rates[list(required)].isna().any().any():
        raise ValueError('Missing FX provenance/value')
    rates = rates.copy()
    days = pd.to_datetime(rates.txn_date, errors='raise')
    observations = pd.to_datetime(rates.rate_date, errors='raise')
    age = (days - observations).dt.days
    values = pd.to_numeric(rates.usd_rate, errors='raise')
    if (rates.duplicated(['txn_date', 'currency']).any() or not np.isfinite(values).all()
            or (values <= 0).any() or (age < 0).any() or (age > max_age_days).any()):
        raise ValueError('Duplicate, nonpositive, future or stale FX rate')
    available = set(zip(days.dt.date, rates.currency))
    expected = {(day.date(), currency) for day in pd.date_range(start, end) for currency in currencies}
    if not expected.issubset(available):
        raise ValueError('Incomplete historical FX coverage')
    return rates


def prepare_inputs(transactions, accounts, output, rows=None, fx_file=None, getter=None):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a fresh input directory')
    output.mkdir(parents=True)
    target = output / 'transactions.csv'
    count = 0
    with Path(transactions).open(encoding='utf-8-sig', newline='') as source, target.open('w', encoding='utf-8', newline='') as dest:
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
    if count == 0 or (rows is not None and count != rows):
        raise ValueError('Empty or incomplete requested input')
    shutil.copyfile(accounts, output / 'accounts.csv')
    start, end, currencies = discover_requirements(target)
    rates = pd.read_csv(fx_file) if fx_file else build_daily_rates(
        currencies, start - timedelta(days=1), end, **({'getter': getter} if getter else {}))
    validate_fx(rates, start - timedelta(days=1), end, currencies)
    write_rates_csv(rates, output / 'fx.csv')
    return {'rows': count, 'start_date': str(start), 'end_date': str(end),
            'currencies': currencies, 'scope': 'smoke-prefix' if rows else 'selected-subset',
            'files': {name: {'sha256': digest(output / name), 'bytes': (output / name).stat().st_size}
                      for name in ['transactions.csv', 'accounts.csv', 'fx.csv']}}
