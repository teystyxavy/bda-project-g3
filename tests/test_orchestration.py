import copy
import json
import re
import sqlite3
from pathlib import Path

import pytest

from bda_pipeline.orchestration import ROWS, definition_values, render_definition

ROOT = Path(__file__).resolve().parents[1]
RUN = 'full-orchestrated-20261006'


def lookup(data, path):
    parts = re.findall(r"(?:^\$|\.)([a-zA-Z_][a-zA-Z_0-9]*)|\['([^']+)'\]", path)
    node = data
    for dotted, bracketed in parts:
        node = node[dotted or bracketed]
    return node


def matches(condition, data):
    if 'And' in condition:
        return all(matches(child, data) for child in condition['And'])
    path = condition['Variable'].replace('$$.Execution.Name', '$.execution_name')
    try:
        value = lookup(data, path)
    except KeyError:
        return condition.get('IsPresent') is False
    if 'IsPresent' in condition:
        return condition['IsPresent']
    for operator in ('BooleanEquals', 'StringEquals', 'NumericEquals'):
        if operator in condition:
            expected = condition[operator]
            return type(value) is type(expected) and value == expected
    raise AssertionError(condition)


def next_state(name, data, states=None):
    states = states or render_definition(ROOT, RUN)['States']
    choice = states[name]
    return next((rule['Next'] for rule in choice['Choices'] if matches(rule, data)), choice['Default'])


def contract_data():
    states = render_definition(ROOT, RUN)['States']
    data = {'execution_name': RUN, 'run_id': RUN, 'expected_rows': ROWS, 'execution_approved': True}
    for name in ('ValidateFrozenJob', 'ValidateCuratedTable', 'ValidateWorkgroup'):
        for rule in states[name]['Choices'][0]['And']:
            if 'IsPresent' in rule:
                continue
            parts = re.findall(r"(?:^\$|\.)([a-zA-Z_][a-zA-Z_0-9]*)|\['([^']+)'\]", rule['Variable'])
            node = data
            for dotted, bracketed in parts[:-1]:
                node = node.setdefault(dotted or bracketed, {})
            node[parts[-1][0] or parts[-1][1]] = next(value for key, value in rule.items() if key.endswith('Equals'))
    return data


@pytest.mark.parametrize('key,bad', [('execution_approved', False), ('execution_approved', 'true'), ('run_id', 'another-run'), ('execution_name', 'another-run'), ('expected_rows', 1024)])
def test_unapproved_or_mismatched_request_rejects_before_compute(key, bad):
    data = contract_data()
    assert next_state('ValidateRequest', data) == 'GetFrozenJob'
    data[key] = bad
    assert next_state('ValidateRequest', data) == 'RequestRejected'
    del data[key]
    assert next_state('ValidateRequest', data) == 'RequestRejected'


@pytest.mark.parametrize('state,field', [('ValidateFrozenJob', 'job'), ('ValidateCuratedTable', 'table'), ('ValidateWorkgroup', 'workgroup')])
def test_every_contract_field_rejects_change_and_absence(state, field):
    data = contract_data()
    assert next_state(state, data) != 'ContractRejected'
    assert next_state(state, {**data, field: {}}) == 'ContractRejected'
    for condition in render_definition(ROOT, RUN)['States'][state]['Choices'][0]['And']:
        if 'IsPresent' in condition:
            continue
        path = condition['Variable']
        parts = re.findall(r"(?:^\$|\.)([a-zA-Z_][a-zA-Z_0-9]*)|\['([^']+)'\]", path)
        changed = copy.deepcopy(data)
        node = changed
        for dotted, bracketed in parts[:-1]:
            node = node[dotted or bracketed]
        key = parts[-1][0] or parts[-1][1]
        node[key] = 'unexpected'
        assert next_state(state, changed) == 'ContractRejected', path
        del node[key]
        assert next_state(state, changed) == 'ContractRejected', path


@pytest.mark.parametrize('state,field', [('OutputEmpty', 'inventory'), ('TempEmpty', 'temp_inventory')])
def test_partial_or_completed_outputs_are_preserved(state, field):
    assert next_state(state, {field: {'KeyCount': 0}}) != 'ExistingOutputRejected'
    assert next_state(state, {field: {'KeyCount': 1}}) == 'ExistingOutputRejected'
    assert next_state(state, {field: {}}) == 'ExistingOutputRejected'


def test_marker_and_failure_paths_do_not_publish_or_retry_compute():
    states = render_definition(ROOT, RUN)['States']
    branches = states['ComputeBusinessOutputs']['Branches']
    scored = branches[0]['States']
    assert next_state('ValidateSuccessMarker', {'marker': {'ContentLength': 0, 'ServerSideEncryption': 'AES256'}}, scored) == 'ScoringComplete'
    for marker in ({}, {'ContentLength': 1, 'ServerSideEncryption': 'AES256'}, {'ContentLength': 0, 'ServerSideEncryption': 'aws:kms'}):
        assert next_state('ValidateSuccessMarker', {'marker': marker}, scored) == 'InvalidMarker'
    assert 'Retry' not in scored['RunFrozenScoring']
    assert 'Retry' not in branches[1]['States']['RunTreasuryCorridors']
    assert states['ComputeBusinessOutputs']['Catch'][0]['Next'] == 'NotifyFailure'
    assert states['NotifyFailure']['Catch'][0]['Next'] == 'WorkflowFailed'
    assert states['BusinessOutputsReady']['Parameters']['dashboard_published'] is False
    assert 'cloudfront' not in json.dumps(states).lower()


def test_corridors_are_directed_daily_fx_normalized_and_not_label_based():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE full_transactions (txn_date TEXT, from_bank TEXT, to_bank TEXT, payment_currency TEXT, receiving_currency TEXT, amount_usd REAL, amount_usd_asof REAL, is_laundering INTEGER)')
    conn.executemany('INSERT INTO full_transactions VALUES (?,?,?,?,?,?,?,?)', [
        ('2022-09-01', '001', '002', 'Euro', 'US Dollar', 60, 999999, 0),
        ('2022-09-01', '001', '002', 'Euro', 'US Dollar', 15, 999999, 1),
        ('2022-09-01', '002', '001', 'US Dollar', 'Euro', 25, 999999, 0),
        ('2022-09-02', '001', '002', 'Euro', 'US Dollar', 40, 999999, 0),
        ('2022-08-31', '001', '002', 'Euro', 'US Dollar', 999999, 999999, 1),
    ])
    rows = [dict(row) for row in conn.execute((ROOT/'sql/full-orchestrated-corridors.sql').read_text(encoding='utf-8'))]
    for kind in ('bank', 'currency'):
        daily = [r for r in rows if r['corridor_type'] == kind and r['txn_date'] == '2022-09-01']
        assert sum(r['transactions'] for r in daily) == 3
        assert sum(r['volume_usd'] for r in daily) == 100
        assert [r['volume_share'] for r in daily] == [0.75, 0.25]
        assert all(r['daily_hhi'] == pytest.approx(0.625) for r in daily)
    assert rows[0]['from_node'] == '001'
    assert len(rows) == 6


@pytest.mark.parametrize('run', ['../unsafe', 'Uppercase', 'x', 'a'*51])
def test_unsafe_prefixes_rejected(run):
    with pytest.raises(ValueError):
        definition_values(run)
