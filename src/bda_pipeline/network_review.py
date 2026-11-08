"""Separate oversized connected networks from bounded investigation cases.

Detection and independent parity checks still cover the complete population.
The unchanged shape gate applies to the actionable case list. Oversized
components remain private review evidence, never silently split or resized.
"""
import numpy as np

from .network import MAX_RING_SHARE, MIN_RING_LIMIT, check_shape


def investigation_view(result):
    accounts = int(result['totals'].accounts.iloc[0])
    limit = max(MIN_RING_LIMIT, int(np.ceil(MAX_RING_SHARE * accounts)))
    sizes = result['ring_members'].groupby('ring_id').size()
    oversized = sizes[sizes > limit]
    ids = set(oversized.index)
    view = dict(result)
    evidence = {}
    for name in ('ring_members', 'ring_edges', 'ring_payments'):
        frame = result[name]
        mask = frame.ring_id.isin(ids)
        evidence[name] = frame.loc[mask].copy()
        view[name] = frame.loc[~mask].copy()
    # Loops/pairs, hub counts, and population totals retain their full scope.
    # Only the connected-component case list is restricted.
    report = dict(
        policy='oversized-components-withheld-v1',
        status='quarantined' if ids else 'not-needed',
        ring_limit=limit, components=len(ids), accounts=int(oversized.sum()),
        largest_component=int(sizes.max()) if len(sizes) else 0,
        groups=[dict(component_id=str(key), accounts=int(size)) for key, size in oversized.items()],
        reason='Connected groups above the unchanged case-size limit require separate network review; '
               'they are excluded from the actionable rings, ranking, graphs and ring coverage. '
               'Full-population transaction, account, hub, pair and loop totals are retained.',
    )
    shape = check_shape(view)
    return view, report, evidence, shape
