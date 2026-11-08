"""Bounded dashboard projections from complete verified populations; no AWS calls.

Called by the export stage and scripts/dashboard/build-exploration.py to enrich
an existing snapshot. BQ1 compares model/baseline review capacity using aggregate
label-based metrics and bounded queues; BQ2 projects corridor volume and
concentration. High corridor volume is not an AML risk score. Inputs must have
passed the caller's provenance checks; this module does not acquire data,
train models or publish assets. Queue exports remove row-level labels."""
import gc
from collections import defaultdict

import numpy as np
import pandas as pd
import pyarrow.dataset as ds

from .analytics import ranking_metrics
from .batch import QUEUE_COLUMNS, evaluation_periods
from .dashboard import records
from .model import INPUT_COLUMNS, rank
from .splits import LEGACY_SPLITS

CAPACITIES = [25, 50, 100, 250]
QUEUE_FIELDS = [*QUEUE_COLUMNS, 'model_score', 'model_evidence',
                'explanation_bias_margin', 'explanation_total_margin']


def scorer_projection(frame, scores, capacity, model=True, subset='HI-Small'):
    ordering = rank(frame[['timestamp', 'source_row_index', 'is_laundering']], scores)
    metrics = ranking_metrics(ordering)
    labels = ordering.is_laundering.to_numpy()
    cumulative = labels.cumsum()
    capacities = {}
    for k in CAPACITIES:
        if k > capacity:
            continue
        n = min(k, len(ordering))
        hits = int(cumulative[n - 1]) if n else 0
        precision = hits / n if n else None
        capacities[str(k)] = {**metrics, 'k': n, 'hits': hits,
            'precision_at_k': precision,
            'recall_at_k': hits / metrics['positives'] if metrics['positives'] else None,
            'lift_at_k': precision / (metrics['positives'] / metrics['rows'])
                if metrics['positives'] and precision is not None else None}
    queue_indices = ordering.index[:capacity]
    daily = frame.assign(day=frame.timestamp.dt.strftime('%Y-%m-%d')).groupby('day').size()
    selected_daily = {}
    for k in capacities:
        selected = ordering.head(int(k)).timestamp.dt.strftime('%Y-%m-%d').value_counts()
        selected_daily[k] = [{'day': day, 'transactions': int(count), 'reviewed': int(selected.get(day, 0))}
                            for day, count in daily.items()]
    edges = np.linspace(0, 1, 21) if model else np.arange(0, 111, 10)
    counts, edges = np.histogram(scores, edges)
    histogram = [{'label': f'{a:.2f}–{b:.2f}', 'transactions': int(n)}
                 for a, b, n in zip(edges[:-1], edges[1:], counts)]
    # PR coordinates at score-tie boundaries. AP above uses all boundaries.
    grouped = ordering.groupby('risk_score').is_laundering.agg(['sum', 'count']).sort_index(ascending=False)
    hits, reviewed = grouped['sum'].cumsum(), grouped['count'].cumsum()
    positions = np.unique(np.linspace(0, max(0, len(grouped) - 1), min(80, len(grouped)), dtype=int))
    curve = [{'recall': float(hits.iloc[i] / metrics['positives']),
              'precision': float(hits.iloc[i] / reviewed.iloc[i])} for i in positions] if metrics['positives'] else []
    return {'metrics_by_k': capacities, 'daily_by_k': selected_daily, 'histogram': histogram,
            'pr_curve': curve, 'queue': (frame.loc[queue_indices, 'transaction_id'].tolist() if 'transaction_id' in frame else [f'{subset}:{i}' for i in frame.loc[queue_indices, 'source_row_index']])}


class RankedPopulation:
    """Exact score-level aggregates and bounded queues, independent of row count."""
    def __init__(self, capacity, model, subset):
        self.capacity, self.model, self.subset = capacity, model, subset
        self.queue = None
        self.levels = None
        self.daily = defaultdict(int)
        self.edges = np.linspace(0, 1, 21) if model else np.arange(0, 111, 10)
        self.histogram = np.zeros(len(self.edges) - 1, dtype=np.int64)

    def add(self, frame, values):
        if frame.empty:
            return
        values = np.asarray(values)
        if len(values) != len(frame) or not np.isfinite(values).all():
            raise ValueError('Invalid prediction scores')
        compact = frame[['timestamp', 'source_row_index', 'is_laundering']].copy()
        compact['risk_score'] = values
        levels = compact.groupby('risk_score').is_laundering.agg(['sum', 'count'])
        self.levels = levels if self.levels is None else self.levels.add(levels, fill_value=0)
        # Numeric day conversion avoids formatting every timestamp as a Python string.
        days, counts = np.unique(frame.timestamp.to_numpy(dtype='datetime64[D]'), return_counts=True)
        for day, count in zip(days, counts):
            self.daily[str(day)] += int(count)
        self.histogram += np.histogram(values, self.edges)[0]
        # Only rows at the batch's top score boundary can enter the global queue.
        if len(values) > self.capacity:
            cutoff = np.partition(values, len(values) - self.capacity)[len(values) - self.capacity]
            positions = np.flatnonzero(values >= cutoff)
        else:
            positions = np.arange(len(values))
        candidate = frame.iloc[positions].copy()
        candidate['_rank_score'] = values[positions]
        candidate = candidate.sort_values(['_rank_score', 'timestamp', 'source_row_index'],
                                          ascending=[False, True, True]).head(self.capacity)
        queue = candidate if self.queue is None else pd.concat([self.queue, candidate], ignore_index=True)
        self.queue = queue.sort_values(['_rank_score', 'timestamp', 'source_row_index'],
                                      ascending=[False, True, True]).head(self.capacity).copy()

    def finish(self):
        grouped = (self.levels.sort_index(ascending=False).astype('int64') if self.levels is not None
                   else pd.DataFrame({'sum': pd.Series(dtype='int64'), 'count': pd.Series(dtype='int64')}))
        n, positives = int(grouped['count'].sum()), int(grouped['sum'].sum())
        hits, reviewed = grouped['sum'].cumsum(), grouped['count'].cumsum()
        ap = float(((grouped['sum'] / positives) * (hits / reviewed)).sum()) if positives else None
        metrics, daily_by_k = {}, {}
        queue = self.queue
        base = dict(rows=n, positives=positives, average_precision=ap)
        for k in CAPACITIES:
            if k > self.capacity:
                continue
            effective = min(k, n)
            count = int(queue.head(k).is_laundering.sum()) if queue is not None else 0
            precision = count / effective if effective else None
            metrics[str(k)] = {**base, 'k': effective, 'hits': count, 'precision_at_k': precision,
                'recall_at_k': count / positives if positives else None,
                'lift_at_k': precision / (positives / n) if positives and precision is not None else None}
            selected = queue.head(k).timestamp.dt.strftime('%Y-%m-%d').value_counts() if queue is not None else {}
            daily_by_k[str(k)] = [{'day': day, 'transactions': total, 'reviewed': int(selected.get(day, 0))}
                                 for day, total in sorted(self.daily.items())]
        positions = np.unique(np.linspace(0, max(0, len(grouped) - 1), min(80, len(grouped)), dtype=int))
        curve = [{'recall': float(hits.iloc[i] / positives), 'precision': float(hits.iloc[i] / reviewed.iloc[i])}
                 for i in positions] if positives else []
        ids = ([] if queue is None else queue.transaction_id.tolist() if 'transaction_id' in queue
               else [f'{self.subset}:{i}' for i in queue.source_row_index])
        return {'metrics_by_k': metrics, 'daily_by_k': daily_by_k,
                'histogram': [{'label': f'{a:.2f}–{b:.2f}', 'transactions': int(count)}
                              for a, b, count in zip(self.edges[:-1], self.edges[1:], self.histogram)],
                'pr_curve': curve, 'queue': ids}


def stream_model_view(dataset, scores, ranker, start, end, subset='HI-Small'):
    population = RankedPopulation(100, True, subset)
    condition = (ds.field('timestamp') >= pd.Timestamp(start)) & (ds.field('timestamp') < pd.Timestamp(end))
    for batch in dataset.to_batches(columns=INPUT_COLUMNS, filter=condition, batch_size=100_000):
        frame = batch.to_pandas()
        population.add(frame, scores[frame.source_row_index.to_numpy(dtype='int64')])
    result = population.finish()
    queue = population.queue
    return {'metrics': result['metrics_by_k']['100'], 'queue':
            records(ranker.queue(queue, queue._rank_score.to_numpy())) if queue is not None else []}


def aml_exploration(dataset, scores, ranker, end_date='2022-09-18', subset='HI-Small', splits=None):
    views, pool, targets = {}, {}, {}
    optional = [c for c in ['amount_paid', 'receiving_currency'] if c in dataset.schema.names and c not in INPUT_COLUMNS]
    for period, (start, end) in evaluation_periods(splits or LEGACY_SPLITS, end_date).items():
        condition = (ds.field('timestamp') >= pd.Timestamp(start)) & (ds.field('timestamp') < pd.Timestamp(end))
        banks, choices = defaultdict(float), {'payment_format': set(), 'payment_currency': set()}
        # Determine offered filters from the entire period, reading only compact columns.
        for batch in dataset.to_batches(columns=['payment_format', 'payment_currency', 'from_bank', 'amount_usd_asof'],
                                        filter=condition, batch_size=100_000):
            frame = batch.to_pandas()
            for column in choices:
                choices[column].update(frame[column].dropna().unique())
            for bank, volume in frame.groupby('from_bank').amount_usd_asof.sum().items():
                banks[bank] += float(volume)
        scopes = [('all', 'Whole evaluation period', None, None)]
        for dimension, title in [('payment_format', 'Payment'), ('payment_currency', 'Currency'), ('from_bank', 'Sender bank')]:
            options = ([bank for bank, _ in sorted(banks.items(), key=lambda pair: (-pair[1], pair[0]))[:5]]
                       if dimension == 'from_bank' else sorted(choices[dimension]))
            scopes.extend((dimension + ':' + str(value), title + ': ' + str(value), dimension, str(value)) for value in options)
        populations = {scope_id: {kind: RankedPopulation(250 if scope_id == 'all' else 100, kind == 'model', subset)
                                 for kind in ['model', 'rules']} for scope_id, _, _, _ in scopes}
        for batch in dataset.to_batches(columns=INPUT_COLUMNS + optional, filter=condition, batch_size=100_000):
            frame = batch.to_pandas().reset_index(drop=True)
            frame['model_score'] = scores[frame.source_row_index.to_numpy(dtype='int64')]
            # Partition once per dimension instead of comparing every row
            # against every currency/format/bank option separately.
            partitions = [('all', frame)]
            for dimension in ('payment_format', 'payment_currency', 'from_bank'):
                requested = {value: scope_id for scope_id, _, dim, value in scopes if dim == dimension}
                selected = frame.loc[frame[dimension].isin(requested)]
                partitions.extend((requested[value], group) for value, group in
                    selected.groupby(dimension, sort=False, observed=True))
            for scope_id, current in partitions:
                for kind, column in [('model', 'model_score'), ('rules', 'risk_score')]:
                    populations[scope_id][kind].add(current, current[column].to_numpy())
        projected = []
        for scope_id, label, dimension, value in scopes:
            item = {'id': scope_id, 'label': label, 'dimension': dimension, 'value': value,
                    'capacity': 250 if scope_id == 'all' else 100}
            for kind, population in populations[scope_id].items():
                item[kind] = population.finish()
                selected = population.queue
                if selected is not None:
                    for row in records(selected[[c for c in QUEUE_COLUMNS + ['model_score'] + optional if c in selected]]):
                        pool.setdefault(row['transaction_id'], {}).update(row)
                    if scope_id == 'all':
                        for row in records(selected[['transaction_id', 'timestamp', 'source_row_index', 'from_bank', 'from_account']]):
                            targets[row['transaction_id']] = row
            selected = populations['all']['model'].queue if scope_id == 'all' else None
            if selected is not None:
                for row in records(ranker.queue(selected, selected.model_score.to_numpy(), k=250)):
                    pool[row['transaction_id']].update(row)
            projected.append(item)
        views[period] = {'scopes': projected}
        del populations
        gc.collect()
    histories = account_histories(dataset, targets)
    return {'periods': views, 'record_fields': QUEUE_FIELDS + ['amount_paid', 'receiving_currency'],
            'records': {key: [row.get(field) for field in QUEUE_FIELDS + ['amount_paid', 'receiving_currency']]
                        for key, row in pool.items()}, 'histories': histories,
            'filter_policy': 'Each population option is independently computed from every transaction in that period. '
                             'Sender-bank options cover the five largest sending banks by prior-day USD volume. '
                             'Search, amount and row filters apply only to the exported ranked queue.'}


def account_histories(dataset, targets):
    if not targets:
        return {}
    senders = {(row['from_bank'], row['from_account']) for row in targets.values()}
    columns = ['transaction_id', 'timestamp', 'source_row_index', 'from_bank', 'from_account',
               'to_bank', 'to_account', 'payment_format', 'amount_usd_asof']
    condition = ds.field('from_bank').isin(sorted({key[0] for key in senders})) & ds.field('from_account').isin(sorted({key[1] for key in senders}))
    # Keep only eight preceding rows per displayed case and exact counts. A
    # high-volume sender must not force its whole history into driver memory.
    by_sender = defaultdict(list)
    counts, tails, observed = {}, {}, set()
    for key, target in targets.items():
        by_sender[(target['from_bank'], target['from_account'])].append((key, target, pd.Timestamp(target['timestamp'])))
        counts[key] = 0
    for batch in dataset.to_batches(columns=columns, filter=condition, batch_size=100_000):
        frame = batch.to_pandas()
        for sender, group in frame.groupby(['from_bank', 'from_account'], sort=False):
            if sender not in by_sender:
                continue
            for key, target, stamp in by_sender[sender]:
                observed.add(key)
                prior = group.loc[(group.timestamp < stamp) |
                    ((group.timestamp == stamp) & (group.source_row_index < target['source_row_index']))]
                counts[key] += len(prior)
                tail = prior.sort_values(['timestamp', 'source_row_index']).tail(8)
                if key in tails:
                    tail = pd.concat([tails[key], tail], ignore_index=True)
                tails[key] = tail.sort_values(['timestamp', 'source_row_index']).tail(8).copy()
    result = {}
    for key in targets:
        if key in observed:
            result[key] = {'observed_prior_transactions': counts[key],
                'events': records(tails[key].drop(columns=['source_row_index', 'from_bank', 'from_account'])),
                'scope': 'Latest eight strictly preceding outgoing transactions in this dataset; composite bank/account key.'}
    return result


def joint_corridors(dataset):
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    partials = []
    for batch in dataset.to_batches(columns=keys + ['amount_usd'], batch_size=100_000):
        frame = batch.to_pandas()
        frame['txn_date'] = frame.txn_date.astype(str)
        partials.append(frame.groupby(keys, observed=True).amount_usd.agg(volume_usd='sum', transaction_count='size').reset_index())
        if len(partials) >= 8:
            partials = [pd.concat(partials, ignore_index=True).groupby(keys, observed=True)[['volume_usd', 'transaction_count']].sum().reset_index()]
    return pd.concat(partials).groupby(keys, observed=True)[['volume_usd', 'transaction_count']].sum().reset_index()


def treasury_exploration(corridors):
    frame = corridors.copy()
    frame['txn_date'] = pd.to_datetime(frame.txn_date).dt.strftime('%Y-%m-%d')
    pair_keys = ['from_bank', 'to_bank']
    banks = frame.groupby(['txn_date'] + pair_keys, observed=True)[['volume_usd', 'transaction_count']].sum().reset_index()
    totals = banks.groupby('txn_date').volume_usd.sum()
    days = sorted(totals.index)
    all_banks = banks.groupby(pair_keys)[['volume_usd', 'transaction_count']].sum().reset_index()
    selected = set(map(tuple, all_banks.nlargest(30, 'volume_usd')[pair_keys].to_numpy()))
    for _, group in banks.groupby('txn_date'):
        selected.update(map(tuple, group.nlargest(15, 'volume_usd')[pair_keys].to_numpy()))
    details = []
    for source, target in sorted(selected):
        subset = frame.loc[frame.from_bank.eq(source) & frame.to_bank.eq(target)]
        daily = subset.groupby('txn_date')[['volume_usd', 'transaction_count']].sum()
        currencies = subset.groupby(['payment_currency', 'receiving_currency'])[['volume_usd', 'transaction_count']].sum().reset_index()
        details.append({'from_bank': str(source), 'to_bank': str(target),
            'daily': [{'day': day, 'volume_usd': float(daily.loc[day, 'volume_usd']) if day in daily.index else 0,
                       'transactions': int(daily.loc[day, 'transaction_count']) if day in daily.index else 0,
                       'share': float(daily.loc[day, 'volume_usd'] / totals[day]) if day in daily.index and totals[day] else 0} for day in days],
            'currencies': records(currencies.nlargest(10, 'volume_usd'))})
    node_volume = defaultdict(float)
    for row in all_banks.itertuples():
        node_volume[str(row.from_bank)] += row.volume_usd
        node_volume[str(row.to_bank)] += row.volume_usd
    nodes = [bank for bank, _ in sorted(node_volume.items(), key=lambda pair: (-pair[1], pair[0]))[:12]]
    heatmaps, comparisons = {}, {}
    for period in ['all', *days]:
        current = banks if period == 'all' else banks.loc[banks.txn_date.eq(period)]
        mapped = current.assign(source=current.from_bank.where(current.from_bank.isin(nodes), 'Other banks'),
                                target=current.to_bank.where(current.to_bank.isin(nodes), 'Other banks'))
        heatmaps[period] = records(mapped.groupby(['source', 'target'])[['volume_usd', 'transaction_count']].sum().reset_index())
        if period == 'all':
            middle = len(days) // 2
            left = banks.loc[banks.txn_date.isin(days[:middle])]
            right = banks.loc[banks.txn_date.isin(days[middle:])]
            label = f'{days[:middle][0]}–{days[:middle][-1]} versus {days[middle:][0]}–{days[middle:][-1]}' if middle else 'No earlier period'
        else:
            i = days.index(period)
            if i == 0:
                comparisons[period] = None
                continue
            left, right = banks.loc[banks.txn_date.eq(days[i - 1])], current
            label = days[i - 1] + ' versus ' + period
        if left.empty or right.empty:
            comparisons[period] = None
            continue
        merged = left.groupby(pair_keys).volume_usd.sum().to_frame('previous').join(
            right.groupby(pair_keys).volume_usd.sum().to_frame('current'), how='outer').fillna(0)
        merged['share_change'] = merged.current / merged.current.sum() - merged.previous / merged.previous.sum()
        merged['volume_change'] = merged.current - merged.previous
        movers = pd.concat([merged.nlargest(6, 'share_change'), merged.nsmallest(6, 'share_change')]).drop_duplicates().reset_index()
        comparisons[period] = {'label': label, 'previous_volume': float(merged.previous.sum()),
                              'current_volume': float(merged.current.sum()), 'movers': records(movers)}
    daily_shares = []
    for day, group in banks.groupby('txn_date'):
        shares = group.volume_usd / group.volume_usd.sum() if group.volume_usd.sum() else group.volume_usd * 0
        daily_shares.append({'day': day, 'top1_share': float(shares.max()), 'top5_share': float(shares.nlargest(5).sum())})
    return {'corridors': details, 'daily_shares': daily_shares, 'heatmap_nodes': nodes + ['Other banks'], 'heatmaps': heatmaps,
            'comparisons': comparisons, 'coverage': 'Complete daily histories for the union of all-period top 30 and daily top 15 corridors. '
                    'Heatmap includes every transaction, with banks outside the twelve leading endpoints grouped as Other banks. '
                    'Movers are ranked across every bank pair. Currency breakdowns are all-period top ten for the selected corridor.'}


def enrich_snapshot(snapshot, dataset, scores, ranker, corridors=None, treasury=True):
    """Add the ``exploration`` section. ``treasury=False`` (the AWS export, where BQ2 is the
    mule-network section) skips the retired treasury corridor exploration."""
    if len(scores) != snapshot.get('source_index_rows', snapshot['full_rows']):
        raise ValueError('Exploration score population mismatch')
    snapshot['exploration'] = {'version': 1, 'aml': aml_exploration(dataset, scores, ranker, snapshot.get('pipeline', {}).get('end_date', '2022-09-18'), snapshot.get('pipeline', {}).get('subset', 'HI-Small'), snapshot.get('splits')),
        'quality': {'status': 'verified-source', 'rows': snapshot['full_rows'],
                    'model_changed': False, 'labels_in_queue': False}}
    if treasury:
        snapshot['exploration']['treasury'] = treasury_exploration(corridors if corridors is not None else joint_corridors(dataset))
    return snapshot


def evaluation_date_breakdown(dataset, scores, development_dates, start, end, subset):
    """Separate previously reported test dates from dates absent from model development.

    Both groups are slices of the frozen model's test window. Neither group
    selects a model, changes thresholds or affects the complete BQ2 population.
    """
    groups = {name: RankedPopulation(100, True, subset) for name in ['previous_test_dates', 'unseen_dates']}
    development_dates = set(development_dates)
    condition = (ds.field('timestamp') >= pd.Timestamp(start)) & (ds.field('timestamp') < pd.Timestamp(end))
    for batch in dataset.to_batches(columns=INPUT_COLUMNS, filter=condition, batch_size=100_000):
        frame = batch.to_pandas()
        previous = frame.timestamp.dt.strftime('%Y-%m-%d').isin(development_dates)
        for name, current in [('previous_test_dates', frame.loc[previous]), ('unseen_dates', frame.loc[~previous])]:
            groups[name].add(current, scores[current.source_row_index.to_numpy(dtype='int64')])
    return {name: {'metrics': population.finish()['metrics_by_k']['100'],
                   'definition': ('Previously included in the original model test report; not new holdout evidence.'
                      if name == 'previous_test_dates' else 'Dates absent from model development inputs; exploratory synthetic-data evaluation without retuning.')}
            for name, population in groups.items()}
