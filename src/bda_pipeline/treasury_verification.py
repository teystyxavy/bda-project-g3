"""Independent daily corridor reconciliation; identifiers stay unchanged."""
import numpy as np
import pandas as pd

KEYS = ['corridor_type', 'txn_date', 'from_node', 'to_node']
METRICS = ['transactions', 'volume_usd', 'volume_share', 'daily_hhi']


def same_query(actual, reviewed):
    # Athena GetQueryExecution omits the final statement delimiter.
    def normalize(sql):
        return sql.strip().removesuffix(';').rstrip()
    return normalize(actual) == normalize(reviewed)


def expected_corridors(batches, expected_rows):
    partials = []
    rows = 0
    for frame in batches:
        required = ['txn_date', 'from_bank', 'to_bank', 'payment_currency',
                    'receiving_currency', 'amount_usd']
        if frame[required].isna().any().any():
            raise ValueError('Missing reference corridor value')
        for column in required[:-1]:
            if not frame[column].map(lambda value: isinstance(value, str)).all():
                raise ValueError('Reference identifiers/dates must be strings')
        if not frame.txn_date.between('2022-09-01', '2022-09-18').all():
            raise ValueError('Reference date outside reviewed scope')
        if not np.isfinite(frame.amount_usd).all() or frame.amount_usd.lt(0).any():
            raise ValueError('Invalid reference USD volume')
        rows += len(frame)
        for kind, source, target in [('bank', 'from_bank', 'to_bank'),
                                     ('currency', 'payment_currency', 'receiving_currency')]:
            grouped = frame.groupby(['txn_date', source, target], observed=True).amount_usd.agg(
                transactions='size', volume_usd='sum').reset_index()
            partials.append(grouped.rename(columns={source: 'from_node', target: 'to_node'}).assign(corridor_type=kind))
    if rows != expected_rows or not partials:
        raise ValueError('Reference population mismatch')
    expected = pd.concat(partials, ignore_index=True).groupby(KEYS, observed=True)[
        ['transactions', 'volume_usd']].sum().reset_index()
    totals = expected.groupby(['corridor_type', 'txn_date']).volume_usd.transform('sum')
    expected['volume_share'] = expected.volume_usd / totals.replace(0, np.nan)
    expected['daily_hhi'] = expected.volume_share.pow(2).groupby(
        [expected.corridor_type, expected.txn_date]).transform(lambda group: group.sum(min_count=1))
    return expected


def compare_corridors(expected, actual, expected_rows):
    if set(actual.columns) != set(KEYS + METRICS) or actual[KEYS].isna().any().any():
        raise ValueError('Unexpected query columns or missing corridor key')
    if actual.duplicated(KEYS).any():
        raise ValueError('Duplicate query corridor')
    merged = expected.merge(actual, on=KEYS, how='outer', suffixes=('_expected', '_actual'), indicator=True)
    if not merged['_merge'].eq('both').all():
        raise ValueError('Missing or unexpected query corridor')
    differences = {}
    for metric in METRICS:
        left = pd.to_numeric(merged[metric + '_expected'], errors='raise').to_numpy(dtype=float)
        right = pd.to_numeric(merged[metric + '_actual'], errors='raise').to_numpy(dtype=float)
        atol = 0.01 if metric == 'volume_usd' else 1e-12
        equal = np.array_equal(left, right) if metric == 'transactions' else np.allclose(left, right, rtol=1e-10, atol=atol, equal_nan=True)
        if not equal:
            raise ValueError('Query metric mismatch: ' + metric)
        finite = np.isfinite(left) & np.isfinite(right)
        differences[metric] = float(np.max(np.abs(left[finite] - right[finite]))) if finite.any() else 0.0
    for kind in ['bank', 'currency']:
        if int(actual.loc[actual.corridor_type.eq(kind), 'transactions'].sum()) != expected_rows:
            raise ValueError('Query population mismatch')
    return dict(status='passed', reference_rows=expected_rows, corridor_rows=len(actual),
                days=sorted(actual.txn_date.unique().tolist()), identifiers_preserved=True,
                max_absolute_differences=differences, volume_rtol=1e-10, volume_atol_usd=0.01)
