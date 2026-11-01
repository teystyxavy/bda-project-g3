import ast
import json
from pathlib import Path

import pandas as pd
import pytest

from bda_pipeline import network
from bda_pipeline.network import analyse_network, compare_network, dashboard_section, ring_table
from network_fixture import factory, network_frame

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def result():
    return analyse_network(factory(network_frame()))


def test_totals_drop_self_transfers(result):
    totals = result['totals'].iloc[0]
    frame = network_frame()
    assert totals.rows == len(frame)
    assert totals.self_transfers == 2
    keys = set(network.account_key(frame.from_bank, frame.from_account)) | set(network.account_key(frame.to_bank, frame.to_account))
    assert totals.accounts == len(keys - {'10:SELF'})
    assert totals.links == int(result['accounts'].fan_out.sum())
    assert totals.laundering_accounts == 5  # S0, S1, H, A, B


def test_hubs_and_high_volume_accounts(result):
    accounts = result['accounts']
    assert accounts.loc['10:H', ['fan_in', 'fan_out']].tolist() == [10, 10]
    candidates = set(result['candidates'].account)
    assert candidates == {'10:H', '20:G'}
    assert accounts.loc['20:G', 'links'] == 120
    section = dashboard_section(result)
    headline = next(row for row in section['thresholds'] if row['headline'])
    assert headline['hubs']['accounts'] == 1 and headline['high_volume']['accounts'] == 1


def test_loops_use_payment_order_within_window(result):
    loops = {tuple(row) for row in result['loops'].itertuples(index=False)}
    assert loops == {('10:A', '10:B', '10:C'), ('10:X', '10:Y', '10:Z')}


def test_pairs_need_a_return_within_window(result):
    assert {tuple(row) for row in result['pairs'].itertuples(index=False)} == {('10:P', '10:Q')}


def test_rings_group_hub_counterparties_and_loops(result):
    members = result['ring_members']
    rings = members.groupby('ring_id').account.apply(set).to_dict()
    hub_ring = next(accounts for accounts in rings.values() if '10:H' in accounts)
    assert len(hub_ring) == 21
    assert {'10:A', '10:B', '10:C'} in rings.values() and {'10:X', '10:Y', '10:Z'} in rings.values()
    # ring_id is the smallest account key in the ring.
    for ring_id, accounts in rings.items():
        assert ring_id == min(accounts)
    assert members.set_index('account').role['10:H'] == 'hub'
    assert members.set_index('account').role['10:B'] == 'loop'
    # High-volume, slow-loop and pair accounts never join rings.
    assert not {'20:G', '10:D', '10:P', '10:U'} & set(members.account)
    ranked = ring_table(result)
    assert ranked.iloc[0].hubs == 1 and ranked['rank'].tolist() == [1, 2, 3]
    s0 = result['ring_edges'].set_index(['src', 'dst']).loc[('10:S0', '10:H')]
    assert s0.txns == 2 and s0.usd == pytest.approx(60.0)


def test_dashboard_section_is_json_and_label_free_per_account(result):
    section = dashboard_section(result)
    text = json.dumps(section, allow_nan=False)
    assert 'is_laundering' not in text and 'touches_laundering' not in text
    graph = section['graphs'][section['ring_list'][0]['ring_id']]
    assert {'id', 'role', 'fan_in', 'fan_out', 'in_usd', 'out_usd'} == set(graph['nodes'][0])
    assert all(set(link) == {'source', 'target', 'txns', 'usd', 'loop'} for link in graph['links'])
    loop_ring = next(row for row in section['ring_list'] if row['loops'])
    assert all(link['loop'] for link in section['graphs'][loop_ring['ring_id']]['links'])
    assert section['rings']['rings'] == 3 and section['loops']['three_account']['loops'] == 2


def test_graph_truncates_to_node_limit(result):
    ring_id = result['ring_members'].set_index('account').ring_id['10:H']
    graph = network.ring_graph(ring_id, result, limit=5)
    assert graph['truncated'] and graph['shown_accounts'] == 5 and graph['nodes'][0]['role'] == 'hub'
    shown = {node['id'] for node in graph['nodes']}
    assert all(link['source'] in shown and link['target'] in shown for link in graph['links'])


def test_compare_network_detects_differences(result):
    actual = {name: result[name].copy() for name in network.TABLES}
    assert compare_network(result, actual, len(network_frame()))['status'] == 'passed'
    broken = {name: frame.copy() for name, frame in actual.items()}
    broken['loops'] = broken['loops'].iloc[:1]
    with pytest.raises(ValueError, match='size differs: loops'):
        compare_network(result, broken, len(network_frame()))
    broken = {name: frame.copy() for name, frame in actual.items()}
    broken['ring_edges'].loc[0, 'usd'] += 1.0
    with pytest.raises(ValueError, match='ring_edges.usd'):
        compare_network(result, broken, len(network_frame()))
    broken = {name: frame.copy() for name, frame in actual.items()}
    broken['ring_members'].loc[0, 'role'] = 'hub' if broken['ring_members'].loc[0, 'role'] != 'hub' else 'loop'
    with pytest.raises(ValueError, match='ring_members.role'):
        compare_network(result, broken, len(network_frame()))
    with pytest.raises(ValueError, match='population'):
        compare_network(result, actual, len(network_frame()) + 1)
    del actual['pairs']
    with pytest.raises(ValueError, match='Missing network table'):
        compare_network(result, actual, len(network_frame()))


def test_rejects_invalid_inputs():
    frame = network_frame()
    bad = frame.copy()
    bad.loc[0, 'is_laundering'] = 2
    with pytest.raises(ValueError, match='binary'):
        analyse_network(factory(bad))
    bad = frame.copy()
    bad.loc[0, 'amount_usd'] = -1.0
    with pytest.raises(ValueError, match='USD'):
        analyse_network(factory(bad))
    bad = frame.copy()
    bad.loc[0, 'to_account'] = None
    with pytest.raises(ValueError, match='Missing'):
        analyse_network(factory(bad))


def test_glue_job_constants_match_reference():
    tree = ast.parse((ROOT / 'jobs' / 'network_mules.py').read_text())
    values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id.isupper() and isinstance(node.value, (ast.Constant, ast.Tuple, ast.List))}
    assert values['HUB_THRESHOLDS'] == network.HUB_THRESHOLDS
    assert values['HEADLINE_THRESHOLD'] == network.HEADLINE_THRESHOLD
    assert values['MAX_LINKS'] == network.MAX_LINKS
    assert values['LOOP_WINDOW_DAYS'] == network.LOOP_WINDOW.days
    assert values['MIN_RING_ACCOUNTS'] == network.MIN_RING_ACCOUNTS
    assert values['TABLE_NAMES'] == list(network.TABLES)


def test_small_run_without_structures_still_builds_a_section():
    frame = network_frame()
    quiet = frame[frame.from_account.isin(['P', 'Q', 'U', 'V'])].reset_index(drop=True)
    result = analyse_network(factory(quiet, parts=2))
    assert result['ring_members'].empty and result['loops'].empty and len(result['pairs']) == 1
    section = dashboard_section(result)
    json.dumps(section, allow_nan=False)
    assert section['rings']['rings'] == 0 and section['ring_list'] == [] and section['graphs'] == {}
    assert compare_network(result, {name: result[name] for name in network.TABLES}, len(quiet))['status'] == 'passed'
