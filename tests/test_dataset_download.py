"""Selection, provenance and launcher checks using tiny offline Kaggle fixtures."""
import csv
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from bda_pipeline.acquisition import HEADER, acquire_dataset
from bda_pipeline.dataset_options import SUBSETS, resolve_subset, selected_files

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/ingestion' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_files(root, subset, version=7):
    cache = root / 'cache' / 'versions' / str(version)
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / f'{subset}_Trans.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        writer.writerow(['2022/09/01 12:00', '001', '000A', '002', '000B', 1,
                         'US Dollar', 1, 'US Dollar', 'Wire', 0])
    (cache / f'{subset}_accounts.csv').write_text(
        'Bank Name,Bank ID,Account Number,Entity ID,Entity Name\nBank,001,000A,E1,Fixture\n')
    (cache / f'{subset}_Patterns.txt').write_text('BEGIN LAUNDERING ATTEMPT - fixture\nEND\n')
    calls = []

    def download(handle, path, force_download):
        calls.append((handle, path, force_download))
        return cache / path

    return download, calls


@pytest.mark.parametrize('subset', SUBSETS)
def test_subsets_pin_one_version_and_reuse_verified_files(tmp_path, subset):
    download, calls = source_files(tmp_path, subset)
    directory = tmp_path / 'dataset'
    first = acquire_dataset(directory, subset, downloader=download, include_patterns=True)
    assert first['dataset_version'] == 7
    assert set(first['files']) == set(selected_files(subset, include_patterns=True))
    assert all(handle.endswith('/versions/7') for handle, _, _ in calls[1:])
    calls.clear()
    assert acquire_dataset(directory, subset, downloader=download, include_patterns=True)['origin'] == 'verified-local-reuse'
    assert not calls


def test_adding_patterns_preserves_csvs_and_repairs_only_changed_file(tmp_path):
    download, calls = source_files(tmp_path, 'HI-Small')
    directory = tmp_path / 'dataset'
    original = acquire_dataset(directory, downloader=download)
    calls.clear()
    extended = acquire_dataset(directory, downloader=download, include_patterns=True)
    assert [path for _, path, _ in calls] == ['HI-Small_Patterns.txt']
    assert calls[0][0].endswith('/versions/7')
    assert all(extended['files'][name] == checksum for name, checksum in original['files'].items())
    default = acquire_dataset(directory, downloader=download)
    assert len(default['files']) == 2
    assert 'HI-Small_Patterns.txt' in json.loads((directory / 'HI-Small_download.json').read_text())['files']
    (directory / 'HI-Small_Patterns.txt').write_text('tampered')
    calls.clear()
    acquire_dataset(directory, downloader=download, include_patterns=True)
    assert [path for _, path, _ in calls] == ['HI-Small_Patterns.txt']


def test_partial_selection_extension_refresh_and_version_change(tmp_path):
    download, calls = source_files(tmp_path, 'LI-Large')
    directory = tmp_path / 'dataset'
    first = acquire_dataset(directory, 'LI-Large', files=['accounts'], downloader=download)
    assert list(first['files']) == ['LI-Large_accounts.csv']
    calls.clear()
    acquire_dataset(directory, 'LI-Large', downloader=download)
    assert [path for _, path, _ in calls] == ['LI-Large_Trans.csv']
    assert calls[0][0].endswith('/versions/7')
    calls.clear()
    acquire_dataset(directory, 'LI-Large', version=7, refresh=True, downloader=download)
    assert len(calls) == 2 and all(force for _, _, force in calls)
    newer, new_calls = source_files(tmp_path, 'LI-Large', version=8)
    assert acquire_dataset(directory, 'LI-Large', version=8, downloader=newer)['dataset_version'] == 8
    assert len(new_calls) == 2


def test_unversioned_csvs_are_not_mixed_with_new_patterns(tmp_path):
    download, calls = source_files(tmp_path, 'HI-Small')
    directory = tmp_path / 'dataset'
    directory.mkdir()
    for name in selected_files('HI-Small'):
        shutil.copyfile(tmp_path / 'cache/versions/7' / name, directory / name)
    assert acquire_dataset(directory, downloader=download)['dataset_version'] is None
    assert not calls
    assert acquire_dataset(directory, downloader=download, include_patterns=True)['dataset_version'] == 7
    assert len(calls) == 3


def test_wrong_requested_version_is_rejected(tmp_path):
    download, _ = source_files(tmp_path, 'HI-Small', version=8)
    with pytest.raises(ValueError, match='differs from requested version'):
        acquire_dataset(tmp_path / 'dataset', version=7, downloader=download)


def test_subset_resolution_and_conflicts():
    assert resolve_subset() == 'HI-Small'
    assert resolve_subset(variant='li', size='medium') == 'LI-Medium'
    assert resolve_subset('li-large', size='large') == 'LI-Large'
    with pytest.raises(ValueError, match='conflicts'):
        resolve_subset('HI-Small', size='Medium')
    with pytest.raises(ValueError):
        selected_files('HI-Small', ['arbitrary-file'])


def test_cloud_build_context_imports_acquisition_with_shared_selection(tmp_path):
    spec = importlib.util.spec_from_file_location('prepare_cloud', ROOT / 'scripts/aws/prepare-cloud-ingestion.py')
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    context = tmp_path / 'context'
    cli.prepare(context)
    result = subprocess.run([sys.executable, '-c', 'from bda_pipeline.cloud_acquisition import acquire'],
                            cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(context)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('script,arguments', [
    ('ingest', ['download', '--variant', 'LI', '--size', 'Medium', '--include-patterns']),
    ('kaggle-download', ['--subset', 'LI-Medium', '--include-patterns']),
])
def test_preview_needs_no_network_or_directory(tmp_path, monkeypatch, capsys, script, arguments):
    cli = load_script(script)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, 'argv', [script, *arguments, '--dry-run'])
    no_download = lambda *a, **k: pytest.fail('Download attempted')
    monkeypatch.setattr('bda_pipeline.acquisition.acquire_dataset', no_download)
    if script == 'ingest':
        monkeypatch.setattr(cli, 'aws_clients', lambda *a: pytest.fail('AWS attempted'))
    else:
        monkeypatch.setattr(cli, 'acquire_dataset', no_download)
    cli.main()
    plan = json.loads(capsys.readouterr().out)
    assert plan['subset'] == 'LI-Medium' and len(plan['files']) == 3
    assert plan['starts_aws_workflow'] is False
    assert not list(tmp_path.iterdir())


def test_download_launcher_smoke_uses_real_acquisition_without_aws(tmp_path, monkeypatch, capsys):
    cli = load_script('ingest')
    download, _ = source_files(tmp_path, 'LI-Small')
    monkeypatch.setattr('bda_pipeline.acquisition.acquire_dataset',
                        lambda *a, **kw: acquire_dataset(*a, downloader=download, **kw))
    monkeypatch.setattr(cli, 'aws_clients', lambda *a: pytest.fail('AWS attempted'))
    directory = tmp_path / 'dataset with spaces'
    monkeypatch.setattr(sys, 'argv', ['ingest', 'download', '--variant', 'LI', '--files', 'patterns',
                                    '--dataset-directory', str(directory)])
    cli.main()
    record = json.loads(capsys.readouterr().out)
    assert record['status'] == 'downloaded-not-submitted'
    assert list(record['files']) == ['LI-Small_Patterns.txt']
    assert (directory / 'LI-Small_Patterns.txt').exists()


@pytest.mark.parametrize('arguments', [
    ['run', '--variant', 'LI', '--size', 'Large'],
    ['run', '--include-patterns'], ['run', '--files', 'accounts'],
    ['run', '--dataset-version', '0'], ['run', '--rows', '-1'], ['run', '--train', '--rows', '10'],
    ['download', '--subset', 'LI-Large', '--size', 'Small'],
])
def test_invalid_selection_fails_before_aws_or_download(monkeypatch, arguments):
    cli = load_script('ingest')
    monkeypatch.setattr(sys, 'argv', ['ingest', *arguments])
    monkeypatch.setattr(cli, 'aws_clients', lambda *a: pytest.fail('AWS attempted'))
    monkeypatch.setattr('bda_pipeline.acquisition.acquire_dataset', lambda *a, **k: pytest.fail('Download attempted'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


@pytest.mark.skipif(not shutil.which('pwsh'), reason='PowerShell unavailable')
@pytest.mark.parametrize('arguments,subset,files,cloud', [
    (['-DownloadOnly', '-Variant', 'li', '-Size', 'medium', '-IncludePatterns', '-Refresh'], 'LI-Medium', 3, False),
    (['-DownloadOnly', '-Subset', 'HI-Large', '-Files', 'Transactions,Patterns'], 'HI-Large', 2, False),
    (['-Refresh'], 'HI-Small', 2, True),
])
def test_powershell_forwards_selection_and_preserves_refresh(tmp_path, arguments, subset, files, cloud):
    values = [str(ROOT / 'scripts/ingestion/ingest.ps1'), '-Python', sys.executable,
              '-DatasetDirectory', str(tmp_path / 'dataset with spaces'), *arguments, '-DryRun']
    def quote(value):
        return "'" + value.replace("'", "''") + "'"
    expression = '& ' + ' '.join(quote(value) if not value.startswith('-') else value for value in values)
    expression = expression.replace("'Transactions,Patterns'", "@('Transactions','Patterns')")
    result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-Command', expression],
                            env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan['subset'] == subset and len(plan['files']) == files
    assert plan['refresh'] == ('-Refresh' in arguments)
    assert plan['starts_aws_workflow'] is False
    assert plan.get('would_start_aws_workflow', False) is cloud
