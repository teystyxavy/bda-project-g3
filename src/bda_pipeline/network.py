"""BQ2 mule-network analysis: hubs, loops and rings from who paid whom.

Business question
-----------------
*Which accounts collect money from many sources and quickly pass it on, or
move it in loops, so the network investigations team knows which accounts to
restrict and which alerts belong to the same case?*

BQ1 ranks single payments. BQ2 looks at the shape of the payment network and
finds **groups of accounts** (rings). One ring is one case to investigate.
Detection uses only who paid whom, when and how much. The ``is_laundering``
label is never used to find, size or rank rings. It is used afterwards, to
check how well the network signals line up with confirmed laundering: as
aggregate rates everywhere, and as a per-account and per-payment flag on the
drawn rings so the dashboard's presenter view can reveal, after the fact,
which accounts in a ring really laundered.

Where this sits in the pipeline
-------------------------------
* ``jobs/network_mules.py`` (Glue Spark job ``is459-g3-ingest-network``) runs
  the same method over the curated Parquet in AWS and writes the tables listed
  in ``TABLES`` to ``pipeline/runs/<run>/network/``.
* ``ingest_compute.export_stage`` (SageMaker Processing) recomputes everything
  with this module (``analyse_network``), checks the Glue tables against it
  (``compare_network``) and builds the dashboard section
  (``dashboard_section``). Nothing is published unless the two routes agree.
* ``scripts/local/run-network-analysis.py`` runs it on a laptop.

Method (all thresholds are constants below so the Spark job can match them)
--------------------------------------------------------------------------
1. **Account links.** Each account is ``<bank without leading zeros>:<account>``.
   Self-transfers are dropped. Repeated payments between the same two
   accounts merge into one directed link (count, USD, first/last time).
2. **Fan-in / fan-out.** Distinct senders into and receivers out of each
   account.
3. **Hubs ("collect from many, pay out to many").** ``fan_in >= T`` and
   ``fan_out >= T``, reported for ``T`` in ``HUB_THRESHOLDS`` (5/10/15) because
   the cut-off is our choice; ``HEADLINE_THRESHOLD`` (10) drives the rings.
   Accounts with more than ``MAX_LINKS`` links (``fan_in + fan_out``) behave
   like payment processors or large merchants, not mules. They are reported
   separately as *high-volume accounts* and never join rings or loops,
   otherwise they would glue unrelated cases into one giant group.
4. **Loops ("money that comes back").** A three-account loop A -> B -> C -> A
   whose payments happen in that order within ``LOOP_WINDOW`` (7 days),
   checked on the individual payment timestamps. A two-account back-and-forth
   (A -> B, then B -> A within 7 days) is reported too, but it is a weak signal
   and does not seed rings.
5. **Rings ("one ring = one case").** Undirected connected components over
   (a) every link between a headline hub and an ordinary-sized account and
   (b) every link of a three-account loop. Rings have at least three
   accounts. ``ring_id`` is the smallest account key in the ring, so it is
   deterministic and identical in Spark and pandas.
6. **Ranking.** Rings are ordered by hubs, then loops, then payments, then
   ``ring_id`` (the order of the business question: collect-and-pass-on
   first, loops second). Labels never affect the order. When this order was
   chosen it was compared with alternatives on the labelled HI-Small data, so
   the ranking evaluation on HI-Small is not an independent test.

Data window: the busy part of HI-Small covers about ten days, so every flow
counted here is already "quick"; loops add an explicit 7-day ordering check.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import time


def progress(phase, **counts):
    print('EXPORT_NETWORK phase=' + phase + ' ' + ' '.join(f'{k}={v}' for k, v in counts.items()), flush=True)

HUB_THRESHOLDS = (5, 10, 15)
HEADLINE_THRESHOLD = 10
MAX_LINKS = 100
LOOP_WINDOW = pd.Timedelta(days=7)
MIN_RING_ACCOUNTS = 3
# Dashboard limits: rings listed and drawn, and nodes drawn per ring.
TOP_RINGS = 50
MAX_GRAPH_NODES = 120
# Payments replayed per drawn ring (earliest first); the rest are counted.
MAX_GRAPH_PAYMENTS = 2000
# Plausibility gate (``check_shape``): a ring holding more than this share of
# all accounts means unrelated accounts were glued together (e.g. thresholds
# that do not suit a new dataset), so the run must not publish. The floor keeps
# tiny demo runs from tripping on noise. Full HI-Small: largest ring 33 accounts
# against a limit of 4,228.
MAX_RING_SHARE = 0.01
MIN_RING_LIMIT = 500
# Columns the analysis reads from curated Parquet.
COLUMNS = ['timestamp', 'from_bank', 'from_account', 'to_bank', 'to_account', 'amount_usd', 'is_laundering']
# Tables shared by the Glue job and this module, with their key columns.
TABLES = {
    'totals': [],
    'candidates': ['account'],
    'pairs': ['a', 'b'],
    'loops': ['a', 'b', 'c'],
    'ring_members': ['account'],
    'ring_edges': ['src', 'dst'],
}
INTEGER_COLUMNS = {'fan_in', 'fan_out', 'in_txns', 'out_txns', 'txns', 'laundering_txns', 'rows',
                   'self_transfers', 'accounts', 'links', 'laundering_accounts'}
FLOAT_COLUMNS = {'in_usd', 'out_usd', 'usd'}


def account_key(bank, account):
    """``<bank without leading zeros>:<account>``, the same rule as ``analytics.bank_key``."""
    stripped = bank.astype('string').str.lstrip('0').replace('', '0')
    return (stripped + ':' + account.astype('string')).astype(object)


def _link_partial(frame):
    """Directed account links for one batch: payments, USD, first/last time, labelled payments."""
    src = account_key(frame.from_bank, frame.from_account)
    dst = account_key(frame.to_bank, frame.to_account)
    keep = (src != dst).to_numpy()
    part = pd.DataFrame({'src': src[keep].to_numpy(), 'dst': dst[keep].to_numpy(),
                         'ts': frame.timestamp.to_numpy()[keep], 'usd': frame.amount_usd.to_numpy(dtype=float)[keep],
                         'lab': frame.is_laundering.to_numpy(dtype=np.int64)[keep]})
    grouped = part.groupby(['src', 'dst'], sort=False).agg(
        txns=('ts', 'size'), usd=('usd', 'sum'), first_ts=('ts', 'min'), last_ts=('ts', 'max'),
        laundering_txns=('lab', 'sum')).reset_index()
    return grouped, int((~keep).sum())


def _merge_links(partials):
    """Re-aggregate partial link tables (a link can appear in many batches)."""
    frame = pd.concat(partials, ignore_index=True)
    return frame.groupby(['src', 'dst'], sort=False).agg(
        txns=('txns', 'sum'), usd=('usd', 'sum'), first_ts=('first_ts', 'min'), last_ts=('last_ts', 'max'),
        laundering_txns=('laundering_txns', 'sum')).reset_index()


def _naive_utc(values):
    """Timestamps as timezone-naive UTC ``datetime64[ns]`` so Spark and pandas compare equal."""
    values = pd.to_datetime(values)
    if getattr(values.dt, 'tz', None) is not None:
        values = values.dt.tz_convert('UTC').dt.tz_localize(None)
    return values.astype('datetime64[ns]')


def build_links(batches):
    """Pass 1: every directed account link plus row and self-transfer counts.

    Args:
        batches: iterable of DataFrames with ``COLUMNS``.

    Returns:
        ``(links, rows, self_transfers)``.

    Raises:
        ValueError: on missing values, non-binary labels or invalid USD amounts.
    """
    levels, rows, self_transfers = [], 0, 0
    for frame in batches:
        if frame[COLUMNS].isna().any().any():
            raise ValueError('Missing network input value')
        if not frame.is_laundering.isin([0, 1]).all():
            raise ValueError('Labels must be binary')
        if not np.isfinite(frame.amount_usd.to_numpy(dtype=float)).all() or (frame.amount_usd < 0).any():
            raise ValueError('Invalid USD amount')
        frame = frame.assign(timestamp=_naive_utc(frame.timestamp))
        part, skipped = _link_partial(frame)
        # Binary compaction: each link participates in at most log2(batches)
        # merges, rather than repeatedly re-grouping the whole accumulated graph.
        level = 0
        while level < len(levels) and levels[level] is not None:
            part = _merge_links([levels[level], part])
            levels[level] = None
            level += 1
        if level == len(levels):
            levels.append(part)
        else:
            levels[level] = part
        rows += len(frame)
        self_transfers += skipped
        if rows % 2_000_000 < len(frame):
            print(f'EXPORT_NETWORK_LINKS rows={rows}', flush=True)
    partials = [part for part in levels if part is not None]
    if not partials:
        raise ValueError('No network input rows')
    progress('merge_links', rows=rows, partials=len(partials))
    links = _merge_links(partials)
    progress('links_complete', rows=rows, links=len(links))
    return links, rows, self_transfers


def account_table(links):
    """Per-account fan-in/fan-out, payments, USD and whether any link carried a labelled payment."""
    out = links.groupby('src').agg(fan_out=('dst', 'size'), out_txns=('txns', 'sum'), out_usd=('usd', 'sum'),
                                   out_lab=('laundering_txns', 'sum'))
    inn = links.groupby('dst').agg(fan_in=('src', 'size'), in_txns=('txns', 'sum'), in_usd=('usd', 'sum'),
                                   in_lab=('laundering_txns', 'sum'))
    accounts = out.join(inn, how='outer')
    for column in ['fan_out', 'out_txns', 'out_lab', 'fan_in', 'in_txns', 'in_lab']:
        accounts[column] = accounts[column].fillna(0).astype(np.int64)
    for column in ['out_usd', 'in_usd']:
        accounts[column] = accounts[column].fillna(0.0)
    accounts['links'] = accounts.fan_in + accounts.fan_out
    accounts['touches_laundering'] = (accounts.out_lab + accounts.in_lab) > 0
    accounts.index.name = 'account'
    return accounts.drop(columns=['out_lab', 'in_lab'])


def _times(transactions):
    """``{(src, dst): sorted int64 nanosecond timestamps}`` for the given payments."""
    result = {}
    for (src, dst), group in transactions.groupby(['src', 'dst'], sort=False):
        result[(src, dst)] = np.sort(group.ts.to_numpy(dtype='datetime64[ns]').astype(np.int64))
    return result


def _next_at_or_after(values, starts):
    """For each start, the first value >= start (or +inf when there is none)."""
    index = np.searchsorted(values, starts, side='left')
    found = np.full(len(starts), np.iinfo(np.int64).max, dtype=np.int64)
    ok = index < len(values)
    found[ok] = values[index[ok]]
    return found


def _ordered_within(window, *legs):
    """True if one payment from each leg happens in order (t1 <= t2 <= ...) within ``window`` of the first.

    Greedy is exact: taking the earliest next payment at or after the previous
    one can only make the last timestamp smaller.
    """
    starts = legs[0]
    current = starts
    for leg in legs[1:]:
        current = _next_at_or_after(leg, current)
    return bool(np.any(current - starts <= window))


def find_pairs(ordinary_links, times):
    """Two-account back-and-forth within the window, as canonical ``(a, b)`` with ``a < b``."""
    window = LOOP_WINDOW.value
    rows = []
    for a, b in candidate_pairs(ordinary_links).itertuples(index=False):
        ab, ba = times[(a, b)], times[(b, a)]
        if _ordered_within(window, ab, ba) or _ordered_within(window, ba, ab):
            rows.append((a, b))
    return pd.DataFrame(rows, columns=['a', 'b']).sort_values(['a', 'b'], ignore_index=True)


def candidate_pairs(ordinary_links):
    """Unordered account pairs linked in both directions (before the time check)."""
    reverse = ordinary_links[['src', 'dst']].rename(columns={'src': 'dst', 'dst': 'src'})
    both = ordinary_links[['src', 'dst']].merge(reverse, on=['src', 'dst'])
    both = both[both.src < both.dst]
    return both.rename(columns={'src': 'a', 'dst': 'b'})[['a', 'b']]


def candidate_loops(ordinary_links, chunk_size=20_000):
    """Directed three-account cycles among ordinary-sized accounts, one canonical rotation each.

    A cycle a -> b -> c -> a is kept in the rotation that starts at its
    smallest account. Timing is checked exactly in ``find_loops`` (any
    rotation may be the one in time order, so nothing is filtered here).
    """
    e = ordinary_links[['src', 'dst']]
    # Bound the two-hop intermediate instead of materializing it for the entire
    # graph. Only closed triangles survive each chunk. Detection is unchanged.
    if chunk_size < 1:
        raise ValueError('chunk_size must be positive')
    starts = e[e.src < e.dst]
    outgoing = e.set_index('src')
    closing = e.rename(columns={'src': 'dst_2', 'dst': 'src_1'})
    closing = pd.MultiIndex.from_frame(closing[['dst_2', 'src_1']])
    found = []
    reported = time.monotonic()
    for offset in range(0, len(starts), chunk_size):
        part = starts.iloc[offset:offset + chunk_size].rename(columns={'src': 'src_1', 'dst': 'dst_1'})
        two = part.join(outgoing.rename(columns={'dst': 'dst_2'}), on='dst_1', how='inner')
        two = two[two.src_1 < two.dst_2]
        matches = closing.get_indexer(pd.MultiIndex.from_frame(two[['dst_2', 'src_1']])) >= 0
        found.append(two.loc[matches, ['src_1', 'dst_1', 'dst_2']])
        if time.monotonic() - reported >= 30:
            progress('loop_candidates', links=min(offset + chunk_size, len(starts)), total=len(starts))
            reported = time.monotonic()
    if not found:
        return pd.DataFrame(columns=['a', 'b', 'c'])
    return (pd.concat(found, ignore_index=True).set_axis(['a', 'b', 'c'], axis=1)
            .drop_duplicates().sort_values(['a', 'b', 'c'], ignore_index=True))


def find_loops(candidates, times):
    """Exact check: some rotation of a -> b -> c -> a has payments in order within the window."""
    window = LOOP_WINDOW.value
    rows = []
    for a, b, c in candidates.itertuples(index=False):
        ab, bc, ca = times[(a, b)], times[(b, c)], times[(c, a)]
        if (_ordered_within(window, ab, bc, ca) or _ordered_within(window, bc, ca, ab)
                or _ordered_within(window, ca, ab, bc)):
            rows.append((a, b, c))
    return pd.DataFrame(rows, columns=['a', 'b', 'c'])


def components(edges):
    """Undirected connected components; returns ``{account: smallest account in its component}``."""
    parent = {}

    def find(node):
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for src, dst in edges:
        for node in (src, dst):
            parent.setdefault(node, node)
        left, right = find(src), find(dst)
        if left != right:
            # Keep the smaller key as root so the root is the component minimum.
            if right < left:
                left, right = right, left
            parent[right] = left
    return {node: find(node) for node in parent}


def analyse_network(batches_factory, checkpoint=None):
    """Run the whole method and return the shared tables (see ``TABLES``) plus derived data.

    Args:
        batches_factory: zero-argument callable returning a fresh iterable of
            DataFrames with ``COLUMNS``. It is called twice: once for the
            links and once for the payment times of candidate loops.

    Returns:
        dict of DataFrames: ``totals`` (one row), ``candidates`` (accounts with
        fan-in and fan-out of at least the lowest threshold, any size),
        ``pairs``, ``loops``, ``ring_members`` and ``ring_edges``, plus
        ``accounts`` (every account, used for evaluation only).
    """
    restored = checkpoint.load('result') if checkpoint else None
    if restored is not None:
        progress('result_restored')
        return restored
    restored = checkpoint.load('links') if checkpoint else None
    if restored is None:
        progress('links_start')
        links, rows, self_transfers = build_links(batches_factory())
        if checkpoint:
            checkpoint.save('links', {'links': links, 'counts': pd.DataFrame([
                {'rows': rows, 'self_transfers': self_transfers}])})
    else:
        links = restored['links']
        rows, self_transfers = (int(restored['counts'].iloc[0][key]) for key in ('rows', 'self_transfers'))
        progress('links_restored', rows=rows, links=len(links))
    progress('accounts_start')
    accounts = account_table(links)
    progress('accounts_complete', accounts=len(accounts))
    ordinary = accounts.index[accounts.links <= MAX_LINKS]
    ordinary_set = set(ordinary)
    ordinary_links = links[links.src.isin(ordinary_set) & links.dst.isin(ordinary_set)]

    low = min(HUB_THRESHOLDS)
    candidates = accounts[(accounts.fan_in >= low) & (accounts.fan_out >= low)]
    candidates = candidates[['fan_in', 'fan_out', 'in_txns', 'out_txns', 'in_usd', 'out_usd']].reset_index()
    candidates = candidates.sort_values('account', ignore_index=True)

    progress('pair_candidates_start', links=len(ordinary_links))
    pair_candidates = candidate_pairs(ordinary_links)
    progress('loop_candidates_start', pairs=len(pair_candidates))
    loop_candidates = candidate_loops(ordinary_links)
    progress('loop_candidates_complete', loops=len(loop_candidates))
    needed = set(zip(pair_candidates.a, pair_candidates.b)) | set(zip(pair_candidates.b, pair_candidates.a))
    for a, b, c in loop_candidates.itertuples(index=False):
        needed.update([(a, b), (b, c), (c, a)])
    hubs = set(accounts.index[(accounts.fan_in >= HEADLINE_THRESHOLD) & (accounts.fan_out >= HEADLINE_THRESHOLD)
                              & (accounts.links <= MAX_LINKS)])
    hub_links = ordinary_links[ordinary_links.src.isin(hubs) | ordinary_links.dst.isin(hubs)]
    # The second pass keeps the timestamps of loop/pair candidate links (for the
    # timing checks) and every payment on a possible ring link (hub links and
    # loop candidate links), so the drawn rings can be replayed payment by payment.
    wanted = pd.DataFrame(sorted(needed), columns=['src', 'dst']).assign(timed=True)
    wanted = wanted.merge(hub_links[['src', 'dst']], on=['src', 'dst'], how='outer')
    wanted['timed'] = wanted.timed.notna() & wanted.timed.astype(bool)
    restored_payments = checkpoint.load('payments') if checkpoint else None
    if restored_payments is None:
        pieces = []
        # Build the candidate-link index once. A full merge re-hashed millions
        # of wanted links for every 200k-row input batch.
        wanted_index = pd.MultiIndex.from_frame(wanted[['src', 'dst']])
        if not wanted_index.is_unique:
            raise ValueError('Duplicate wanted payment link')
        wanted_timed = wanted.timed.to_numpy(dtype=bool)
        progress('payment_pass_start', wanted=len(wanted))
        payment_rows, reported = 0, time.monotonic()
        for frame in batches_factory():
            src = account_key(frame.from_bank, frame.from_account)
            dst = account_key(frame.to_bank, frame.to_account)
            positions = wanted_index.get_indexer(pd.MultiIndex.from_arrays([src, dst]))
            matched = positions >= 0
            chosen = frame.loc[matched]
            pieces.append(pd.DataFrame({'src': src.to_numpy()[matched], 'dst': dst.to_numpy()[matched],
                'ts': _naive_utc(chosen.timestamp).to_numpy(), 'usd': chosen.amount_usd.to_numpy(dtype=float),
                'lab': chosen.is_laundering.to_numpy(dtype=np.int64), 'timed': wanted_timed[positions[matched]]}))
            payment_rows += len(frame)
            if time.monotonic() - reported >= 30:
                progress('payment_pass', rows=payment_rows, total=rows)
                reported = time.monotonic()
        if payment_rows != rows:
            raise ValueError('Payment scan population differs from link aggregation')
        progress('payment_pass_complete', rows=payment_rows)
        seen = (pd.concat(pieces, ignore_index=True) if pieces
                else pd.DataFrame({'src': [], 'dst': [], 'ts': pd.Series([], dtype='datetime64[ns]'), 'usd': [], 'lab': [], 'timed': []}))
        del pieces, wanted_index, wanted_timed
        if checkpoint:
            checkpoint.save('payments', {'seen': seen})
    else:
        seen = restored_payments['seen']
        progress('payments_restored', rows=len(seen))
    progress('payment_times_start', rows=len(seen))
    times = _times(seen.loc[seen.timed.astype(bool), ['src', 'dst', 'ts']])
    progress('timing_checks_start', pairs=len(pair_candidates), loops=len(loop_candidates))
    pairs = find_pairs(ordinary_links, times)
    loops = find_loops(loop_candidates, times).sort_values(['a', 'b', 'c'], ignore_index=True)
    del times, needed, wanted, pair_candidates, loop_candidates
    progress('timing_checks_complete', pairs=len(pairs), loops=len(loops))

    loop_links = pd.DataFrame([(s, d) for a, b, c in loops.itertuples(index=False) for s, d in [(a, b), (b, c), (c, a)]],
                              columns=['src', 'dst'])
    ring_links = pd.concat([hub_links[['src', 'dst']], loop_links], ignore_index=True).drop_duplicates()
    ring_links = ring_links.merge(links, on=['src', 'dst'])
    label = components(zip(ring_links.src, ring_links.dst))
    progress('components_complete', accounts=len(label))
    sizes = pd.Series(label).value_counts()
    keep = {node for node, root in label.items() if sizes[root] >= MIN_RING_ACCOUNTS}
    loop_members = set(loops.a) | set(loops.b) | set(loops.c)
    # object dtype even when empty (small demo runs often have no rings).
    members = pd.DataFrame({'account': pd.Series(sorted(keep), dtype=object)})
    members['ring_id'] = members.account.map(label)
    members['role'] = np.where(members.account.isin(hubs), 'hub',
                               np.where(members.account.isin(loop_members), 'loop', 'counterparty'))
    members = members.join(accounts[['fan_in', 'fan_out', 'in_usd', 'out_usd']], on='account')
    ring_edges = ring_links[ring_links.src.isin(keep)].copy()
    ring_edges['ring_id'] = ring_edges.src.map(label)
    ring_edges = ring_edges[['src', 'dst', 'ring_id', 'txns', 'usd', 'first_ts', 'last_ts', 'laundering_txns']]
    ring_edges = ring_edges.sort_values(['src', 'dst'], ignore_index=True)
    ring_payments = ring_payment_table(seen, ring_edges)
    del seen
    progress('ring_payments_complete', rows=len(ring_payments))

    totals = pd.DataFrame([dict(rows=rows, self_transfers=self_transfers, accounts=len(accounts), links=len(links),
                                laundering_accounts=int(accounts.touches_laundering.sum()))])
    result = dict(totals=totals, candidates=candidates, pairs=pairs, loops=loops, ring_members=members,
                  ring_edges=ring_edges, accounts=accounts, ring_payments=ring_payments)
    if checkpoint:
        checkpoint.save('result', result)
    progress('complete', rings=members.ring_id.nunique(), loops=len(loops))
    return result


def ring_payment_table(seen, ring_edges):
    """Every individual payment on a ring link, checked against the link totals.

    Args:
        seen: payments kept by the second pass (``src, dst, ts, usd, lab``).
        ring_edges: the ``ring_edges`` table.

    Returns:
        DataFrame ``ring_id, src, dst, ts, usd, lab`` sorted by ring, time and link.
        Not one of the shared Glue ``TABLES``: it feeds the dashboard only.

    Raises:
        ValueError: if any ring link's payment count, USD total or labelled
        payment count differs from ``ring_edges`` (the two are built from
        different passes over the input, so this is a real cross-check).
    """
    payments = seen[['src', 'dst', 'ts', 'usd', 'lab']].merge(ring_edges[['src', 'dst', 'ring_id']], on=['src', 'dst'])
    found = payments.groupby(['src', 'dst']).agg(n=('ts', 'size'), paid=('usd', 'sum'), labelled=('lab', 'sum'))
    check = ring_edges.set_index(['src', 'dst'])[['txns', 'usd', 'laundering_txns']].join(found, how='left')
    check = check.fillna({'n': 0, 'paid': 0.0, 'labelled': 0})
    bad = ((check.n != check.txns) | (check.labelled != check.laundering_txns)
           | ((check.paid - check.usd).abs() > 0.01 + 1e-9 * check.usd.abs()))
    if bad.any():
        raise ValueError('Ring payments do not reconcile with ring_edges')
    payments = payments[['ring_id', 'src', 'dst', 'ts', 'usd', 'lab']]
    return payments.sort_values(['ring_id', 'ts', 'src', 'dst', 'usd'], ignore_index=True)


def ring_table(result):
    """One row per ring with label-free measures, rank and aggregate evaluation columns."""
    members, edges, loops = result['ring_members'], result['ring_edges'], result['loops']
    touches = result['accounts'].touches_laundering
    by_ring = members.assign(hub=members.role.eq('hub'), touched=members.account.map(touches)).groupby('ring_id').agg(
        accounts=('account', 'size'), hubs=('hub', 'sum'), laundering_accounts=('touched', 'sum'))
    loop_ring = loops.a.map(dict(zip(members.account, members.ring_id)))
    by_ring['loops'] = loop_ring.value_counts().reindex(by_ring.index, fill_value=0)
    flows = edges.groupby('ring_id').agg(links=('src', 'size'), txns=('txns', 'sum'), usd=('usd', 'sum'),
                                         first_ts=('first_ts', 'min'), last_ts=('last_ts', 'max'),
                                         laundering_txns=('laundering_txns', 'sum'))
    rings = by_ring.join(flows).reset_index()
    for column in ['accounts', 'hubs', 'loops', 'laundering_accounts', 'links', 'txns', 'laundering_txns']:
        rings[column] = rings[column].astype(np.int64)
    rings['red_flags'] = rings.hubs + rings.loops
    rings = rings.sort_values(['hubs', 'loops', 'txns', 'ring_id'], ascending=[False, False, False, True], ignore_index=True)
    rings['rank'] = np.arange(1, len(rings) + 1)
    return rings


def _rate(numerator, denominator):
    return float(numerator / denominator) if denominator else None


def ring_graph(ring_id, result, limit=MAX_GRAPH_NODES, payment_limit=MAX_GRAPH_PAYMENTS):
    """Nodes, links and payment timeline of one ring for the dashboard's network view.

    Hubs and loop accounts are always drawn; counterparties are added by
    payment volume until ``limit`` nodes.

    For the presenter view's replay and reveal, each node carries
    ``touches_laundering`` and the graph carries ``payments``: one
    ``[link index, minutes after payment_start, usd, laundering 0/1]`` row per
    payment on a drawn link, earliest first, at most ``payment_limit`` rows
    (``payments_total`` counts them all). ``loops`` lists the drawn timed
    loops as ``[a, b, c]`` account IDs. These flags are shown only after the
    ring was found; they never affect which accounts are drawn or their order.
    """
    members = result['ring_members'][result['ring_members'].ring_id.eq(ring_id)]
    edges = result['ring_edges'][result['ring_edges'].ring_id.eq(ring_id)]
    volume = pd.concat([edges.groupby('src').usd.sum(), edges.groupby('dst').usd.sum()]).groupby(level=0).sum()
    members = members.assign(volume=members.account.map(volume).fillna(0.0),
                             priority=members.role.map({'hub': 0, 'loop': 1, 'counterparty': 2}))
    members = members.sort_values(['priority', 'volume', 'account'], ascending=[True, False, True])
    shown = members.head(limit)
    keep = set(shown.account)
    drawn = edges[edges.src.isin(keep) & edges.dst.isin(keep)]
    loops = result['loops']
    loop_edges = {(s, d) for a, b, c in loops.itertuples(index=False) if a in keep
                  for s, d in [(a, b), (b, c), (c, a)]}
    touches = result['accounts'].touches_laundering
    nodes = [dict(id=row.account, role=row.role, fan_in=int(row.fan_in), fan_out=int(row.fan_out),
                  in_usd=float(row.in_usd), out_usd=float(row.out_usd), touches_laundering=bool(touches.get(row.account, False)))
             for row in shown.itertuples(index=False)]
    links = [dict(source=row.src, target=row.dst, txns=int(row.txns), usd=float(row.usd),
                  loop=(row.src, row.dst) in loop_edges) for row in drawn.itertuples(index=False)]
    link_index = {(link['source'], link['target']): i for i, link in enumerate(links)}
    paid = result['ring_payments']
    paid = paid[paid.ring_id.eq(ring_id)]
    paid = paid[[pair in link_index for pair in zip(paid.src, paid.dst)]]
    start = paid.ts.min().floor('min') if len(paid) else None
    payments = [[link_index[(row.src, row.dst)], int((row.ts - start) // pd.Timedelta(minutes=1)), round(float(row.usd), 2), int(row.lab)]
                for row in paid.head(payment_limit).itertuples(index=False)]
    drawn_loops = [[a, b, c] for a, b, c in loops.itertuples(index=False) if a in keep and b in keep and c in keep]
    return dict(nodes=nodes, links=links, total_accounts=len(members), shown_accounts=len(shown),
                truncated=len(shown) < len(members), loops=drawn_loops,
                payment_start=start.isoformat() if start is not None else None,
                payments=payments, payments_total=int(len(paid)))


def dashboard_section(result, top=TOP_RINGS):
    """The ``network`` section of ``dashboard.json`` (schema 2).

    Labels appear as aggregate rates ("share of these accounts that touch a
    laundering payment") and, inside the drawn ring graphs only, as a
    per-account and per-payment flag for the presenter view's reveal step
    (see ``ring_graph``). They never change detection or ranking.
    """
    accounts, totals = result['accounts'], result['totals'].iloc[0]
    base = _rate(totals.laundering_accounts, totals.accounts)
    touches = accounts.touches_laundering

    def group(index):
        index = list(index)
        hits = int(touches.reindex(index).sum()) if index else 0
        return dict(accounts=len(index), laundering_accounts=hits, laundering_rate=_rate(hits, len(index)),
                    lift=_rate(_rate(hits, len(index)) or 0, base) if index and base else None)

    thresholds = []
    for threshold in HUB_THRESHOLDS:
        both = accounts[(accounts.fan_in >= threshold) & (accounts.fan_out >= threshold)]
        thresholds.append(dict(threshold=threshold, headline=threshold == HEADLINE_THRESHOLD,
                               hubs=group(both.index[both.links <= MAX_LINKS]),
                               high_volume=group(both.index[both.links > MAX_LINKS]),
                               fan_in_only=group(accounts.index[accounts.fan_in >= threshold]),
                               fan_out_only=group(accounts.index[accounts.fan_out >= threshold])))
    loops, pairs = result['loops'], result['pairs']
    rings = ring_table(result)
    members = result['ring_members']
    ring_rows = []
    for row in rings.head(top).itertuples(index=False):
        ring_rows.append(dict(rank=int(row.rank), ring_id=row.ring_id, accounts=int(row.accounts), hubs=int(row.hubs),
                              loops=int(row.loops), red_flags=int(row.red_flags), links=int(row.links),
                              txns=int(row.txns), usd=float(row.usd),
                              first_seen=row.first_ts.isoformat(), last_seen=row.last_ts.isoformat(),
                              laundering_rate=_rate(row.laundering_accounts, row.accounts)))
    top_k = []
    for k in [10, 25, 50]:
        chosen = rings.head(k)
        top_k.append(dict(rings=int(len(chosen)), accounts=int(chosen.accounts.sum()),
                          laundering_rate=_rate(int(chosen.laundering_accounts.sum()), int(chosen.accounts.sum())),
                          rings_with_laundering=int((chosen.laundering_accounts > 0).sum())))
    in_rings = set(members.account)
    return dict(
        schema_version=2,
        question='Which accounts collect money from many sources and quickly pass it on, or move it in loops, '
                 'so we know which accounts to restrict and which alerts belong to the same case?',
        audience='Mule-account and network investigations team',
        parameters=dict(hub_thresholds=list(HUB_THRESHOLDS), headline_threshold=HEADLINE_THRESHOLD,
                        max_links=MAX_LINKS, loop_window_days=LOOP_WINDOW.days, min_ring_accounts=MIN_RING_ACCOUNTS),
        totals=dict(rows=int(totals.rows), self_transfers=int(totals.self_transfers), accounts=int(totals.accounts),
                    links=int(totals.links), laundering_accounts=int(totals.laundering_accounts), laundering_rate=base),
        thresholds=thresholds,
        loops=dict(three_account=dict(loops=len(loops), **group(set(loops.a) | set(loops.b) | set(loops.c))),
                   two_account=dict(pairs=len(pairs), **group(set(pairs.a) | set(pairs.b)))),
        rings=dict(rings=int(len(rings)), accounts=len(in_rings), largest=int(rings.accounts.max()) if len(rings) else 0,
                   coverage=_rate(int(touches.reindex(list(in_rings)).sum()), totals.laundering_accounts),
                   **{key: value for key, value in group(in_rings).items() if key != 'accounts'}),
        top_rings=top_k,
        ring_sizes=[int(size) for size in rings.accounts],
        ring_list=ring_rows,
        graphs={row['ring_id']: ring_graph(row['ring_id'], result) for row in ring_rows},
        definitions=dict(
            hub=f'Fan-in and fan-out of at least the threshold (distinct senders and receivers), with at most {MAX_LINKS} links in total.',
            high_volume=f'Accounts above {MAX_LINKS} links that also pass the threshold. They behave like processors or large merchants, are listed for review, and never join rings.',
            loop=f'A -> B -> C -> A with payments in that order within {LOOP_WINDOW.days} days, among accounts with at most {MAX_LINKS} links.',
            ring=f'Accounts connected through a hub (threshold {HEADLINE_THRESHOLD}) or a loop. One ring = one case; at least {MIN_RING_ACCOUNTS} accounts.',
            ranking='Hubs first, then loops, then payments. Labels never change the order.',
            evaluation='Laundering rate = share of accounts with at least one labelled laundering payment. Used only to check results; '
                       'the presenter view also reveals it per account in the drawn rings, after they were found.'),
    )


def check_shape(result):
    """Plausibility gate on verified network results; returns their shape.

    ``compare_network`` proves Glue and pandas agree; this checks that what
    they agree on is believable. It runs on label-free counts only.

    Returns:
        ``{'accounts', 'rings', 'ring_accounts', 'largest_ring', 'ring_limit',
        'loops', 'pairs', 'status': 'passed'}`` (stored in the network proof).

    Raises:
        ValueError: if one ring holds more than ``MAX_RING_SHARE`` of all
        accounts (and more than ``MIN_RING_LIMIT``).
    """
    accounts = int(result['totals'].accounts.iloc[0])
    sizes = result['ring_members'].groupby('ring_id').size()
    largest = int(sizes.max()) if len(sizes) else 0
    limit = max(MIN_RING_LIMIT, int(np.ceil(MAX_RING_SHARE * accounts)))
    if largest > limit:
        raise ValueError(f'Implausible network: one ring has {largest} accounts (limit {limit}); '
                         'review the thresholds for this dataset before publishing')
    return dict(status='passed', accounts=accounts, rings=int(len(sizes)), ring_accounts=int(sizes.sum()),
                largest_ring=largest, ring_limit=limit, loops=int(len(result['loops'])), pairs=int(len(result['pairs'])))


def _normalise(table, name):
    frame = table.copy()
    for column in frame.columns:
        if column in INTEGER_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors='raise').astype(np.int64)
        elif column in FLOAT_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors='raise').astype(float)
        elif column.endswith('_ts'):
            frame[column] = _naive_utc(frame[column])
        else:
            frame[column] = frame[column].astype(object)
    keys = TABLES[name]
    return frame.sort_values(keys, ignore_index=True) if keys else frame.reset_index(drop=True)


def compare_network(expected, actual, expected_rows):
    """Check the Glue tables against this module's recomputation.

    Keys and every integer column must match exactly; USD sums within 1 cent
    or 1e-9 relative (Spark adds in a different order); timestamps exactly.

    Returns:
        ``status='passed'`` proof dict with table sizes and the largest USD gap.

    Raises:
        ValueError: on a missing table, different columns, a different key
        set, any mismatch, or a row total other than ``expected_rows``.
    """
    differences, sizes = {}, {}
    for name, keys in TABLES.items():
        if name not in actual:
            raise ValueError('Missing network table: ' + name)
        left, right = _normalise(expected[name], name), _normalise(actual[name], name)
        if set(left.columns) != set(right.columns):
            raise ValueError('Network table columns differ: ' + name)
        right = right[left.columns]
        if keys and right.duplicated(keys).any():
            raise ValueError('Duplicate network key: ' + name)
        if len(left) != len(right):
            raise ValueError('Network table size differs: ' + name)
        gap = 0.0
        for column in left.columns:
            if column in FLOAT_COLUMNS:
                if not np.allclose(left[column], right[column], rtol=1e-9, atol=0.01):
                    raise ValueError(f'Network value mismatch: {name}.{column}')
                if len(left):
                    gap = max(gap, float(np.max(np.abs(left[column].to_numpy() - right[column].to_numpy()))))
            elif not left[column].equals(right[column]):
                raise ValueError(f'Network value mismatch: {name}.{column}')
        sizes[name], differences[name] = len(left), gap
    if int(expected['totals'].rows.iloc[0]) != expected_rows:
        raise ValueError('Network population mismatch')
    return dict(status='passed', reference_rows=expected_rows, table_rows=sizes,
                max_absolute_usd_differences=differences, usd_rtol=1e-9, usd_atol=0.01,
                labels_used_for='aggregate evaluation only')


def parquet_batches(path, batch_size=200_000):
    """Factory for ``analyse_network``: streams ``COLUMNS`` from a curated Parquet dataset."""
    import pyarrow.dataset as ds

    dataset = ds.dataset(path, format='parquet', partitioning='hive')

    def batches():
        for batch in dataset.to_batches(columns=COLUMNS, batch_size=batch_size):
            yield batch.to_pandas()
    return batches


def read_tables(path):
    """Read the Glue job's output tables (one Parquet folder per table)."""
    from pathlib import Path

    path = Path(path)
    return {name: pd.read_parquet(path / name) for name in TABLES if (path / name).exists()}
