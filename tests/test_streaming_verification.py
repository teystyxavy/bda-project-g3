"""Local audit fixtures exercise acceptance guards; these are not cloud evidence."""
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def audit_fixture(tmp_path, monkeypatch):
    module_path = Path(__file__).parents[1] / 'scripts/verify-streaming-demo.py'
    spec = importlib.util.spec_from_file_location('stream_verifier', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    package = tmp_path / 'package'
    audit = tmp_path / 'audit'
    package.mkdir()
    audit.mkdir()
    rows = [{'transaction_id': f'HI-Small:{i}', 'features': {'velocity_1h': i},
             'model_score': 0.2, 'run_id': 'fixture'} for i in range(2)]
    payload = '\n'.join(json.dumps(row) for row in rows) + '\n'
    (package / 'results.jsonl').write_text(payload, encoding='utf-8')
    (package / 'verification.json').write_text(json.dumps({
        'run_id': 'fixture', 'results_sha256': hashlib.sha256((package / 'results.jsonl').read_bytes()).hexdigest()
    }), encoding='utf-8')
    for i, row in enumerate(rows):
        (audit / f'{i}.json').write_text(json.dumps(row), encoding='utf-8')
    receipt = tmp_path / 'receipt.jsonl'
    receipt.write_text(payload, encoding='utf-8')
    output = tmp_path / 'report.json'
    monkeypatch.setattr(sys, 'argv', ['verify', '--package', str(package), '--audit', str(audit),
                                    '--receipt', str(receipt), '--output', str(output)])
    return module.main, audit, receipt, output, rows


def test_accepts_matching_audit(audit_fixture):
    main, _, _, output, _ = audit_fixture
    main()
    report = json.loads(output.read_text())
    assert report['rows'] == 2 and report['features_exact']
    assert len(report['audit_sha256']) == 2


@pytest.mark.parametrize('change', ['features', 'score', 'identity', 'missing', 'receipt'])
def test_rejects_corrupted_or_incomplete_audit(audit_fixture, change):
    main, audit, receipt, output, rows = audit_fixture
    row = rows[0].copy()
    if change == 'features': row['features'] = {'velocity_1h': 999}
    if change == 'score': row['model_score'] = 0.9
    if change == 'identity': row['transaction_id'] = rows[1]['transaction_id']
    (audit / '0.json').write_text(json.dumps(row), encoding='utf-8')
    if change == 'missing': (audit / '1.json').unlink()
    if change == 'receipt': receipt.write_text(json.dumps(rows[0]) + '\n', encoding='utf-8')
    with pytest.raises(ValueError): main()
    assert not output.exists()
