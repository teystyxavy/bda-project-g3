import json

import pandas as pd
import pytest

from bda_pipeline import network, network_review
from network_fixture import factory, network_frame


def test_oversized_groups_are_withheld_without_changing_detection(monkeypatch):
    raw = network.analyse_network(factory(network_frame()))
    original = {key: frame.copy(deep=True) for key, frame in raw.items()}
    # Fixture has a 21-account hub component and two 3-account loop components.
    monkeypatch.setattr(network, 'MIN_RING_LIMIT', 5)
    monkeypatch.setattr(network_review, 'MIN_RING_LIMIT', 5)
    with pytest.raises(ValueError, match='Implausible network'):
        network.check_shape(raw)
    view, report, evidence, shape = network_review.investigation_view(raw)
    assert report['components'] == 1 and report['accounts'] == 21
    assert report['status'] == 'quarantined' and shape['status'] == 'passed'
    assert shape['largest_ring'] == 3
    assert len(evidence['ring_members']) == 21
    excluded = set(evidence['ring_members'].ring_id)
    for key in ('ring_members', 'ring_edges', 'ring_payments'):
        assert not set(view[key].ring_id) & excluded
        combined = pd.concat([view[key], evidence[key]]).sort_index()
        pd.testing.assert_frame_equal(combined, raw[key].sort_index())
    for key in ('totals', 'accounts', 'loops', 'pairs', 'candidates'):
        pd.testing.assert_frame_equal(view[key], raw[key])
    for key in raw:
        pd.testing.assert_frame_equal(raw[key], original[key])
    # Independent full-population parity is unaffected; the smaller view is
    # never substituted for the raw detection tables in this check.
    network.compare_network(raw, {name: raw[name] for name in network.TABLES}, len(network_frame()))
    section = network.dashboard_section(view)
    assert section['rings']['largest'] == 3 and section['rings']['accounts'] == 6
    assert not excluded & set(section['graphs'])
    json.dumps(dict(section=section, review=report), allow_nan=False)


def test_plausible_network_is_unchanged():
    raw = network.analyse_network(factory(network_frame()))
    view, report, evidence, shape = network_review.investigation_view(raw)
    assert report['status'] == 'not-needed' and report['components'] == 0
    assert all(frame.empty for frame in evidence.values())
    assert shape == network.check_shape(raw)
    for name in raw:
        pd.testing.assert_frame_equal(view[name], raw[name])


def test_limit_boundary_and_all_groups_quarantined(monkeypatch):
    raw = network.analyse_network(factory(network_frame()))
    monkeypatch.setattr(network, 'MIN_RING_LIMIT', 3)
    monkeypatch.setattr(network_review, 'MIN_RING_LIMIT', 3)
    view, _, _, shape = network_review.investigation_view(raw)
    assert shape['largest_ring'] == 3  # Exact-limit components remain valid.
    monkeypatch.setattr(network, 'MIN_RING_LIMIT', 2)
    monkeypatch.setattr(network_review, 'MIN_RING_LIMIT', 2)
    view, report, _, shape = network_review.investigation_view(raw)
    assert report['components'] == 3 and shape['rings'] == 0
    assert network.dashboard_section(view)['ring_list'] == []
