"""Independent daily corridor reconciliation; identifiers stay unchanged.

Where this sits in the pipeline
-------------------------------
BQ2 (treasury / correspondent-bank corridors) is answered in AWS by the
Athena query ``sql/full-orchestrated-corridors.sql``. For each day it returns
two kinds of "corridor" (a directed from -> to pair):

* ``bank``     from_bank -> to_bank
* ``currency`` payment_currency -> receiving_currency

with transaction count, USD volume, the corridor's share of that day's
volume and the day's Herfindahl-Hirschman Index (HHI = sum of squared shares).

This module recomputes the same table independently in pandas from the
curated reference data, then compares it with Athena's CSV result row by row.
If both routes agree, the dashboard's treasury numbers are trustworthy.

Callers:
    * ``scripts/validation/verify-orchestrated-corridors.py`` (local check of
      a downloaded Athena result plus the ``GetQueryExecution`` report);
    * ``ingest_compute`` inside SageMaker Processing (before release).

Bank IDs are compared as the original strings (e.g. leading zeros kept);
nothing is renamed or masked ("identifiers stay unchanged").
"""
import numpy as np
import pandas as pd

# Columns that identify one corridor row in the Athena output.
KEYS = ['corridor_type', 'txn_date', 'from_node', 'to_node']
# Numeric columns that must match between pandas and Athena.
METRICS = ['transactions', 'volume_usd', 'volume_share', 'daily_hhi']


def same_query(actual, reviewed):
    """Return True if the SQL Athena ran is the reviewed SQL file.

    Args:
        actual: ``QueryExecution.Query`` text from Athena.
        reviewed: contents of ``sql/full-orchestrated-corridors.sql``.

    Only surrounding whitespace and one trailing ``;`` are ignored, so any
    real change to the query text makes the check fail.
    """
    # Athena GetQueryExecution omits the final statement delimiter.
    def normalize(sql):
        """Strip whitespace and a single trailing semicolon."""
        return sql.strip().removesuffix(';').rstrip()
    return normalize(actual) == normalize(reviewed)


def expected_corridors(batches, expected_rows):
    """Compute the reference corridor table, mirroring the Athena SQL.

    Args:
        batches: iterable of DataFrames with ``txn_date`` (string
            'YYYY-MM-DD'), ``from_bank``, ``to_bank``, ``payment_currency``,
            ``receiving_currency`` (strings) and ``amount_usd`` (float). Given
            as batches so the full ~5M rows never sit in memory at once.
        expected_rows: number of transactions the batches must add up to.

    Returns:
        DataFrame with ``KEYS + METRICS`` columns, one row per corridor per
        day per corridor type.

    Raises:
        ValueError: on missing values, non-string IDs/dates, a date outside
        2022-09-01..2022-09-18 (the SQL's WHERE range), negative or
        non-finite USD amounts, or a row total different from expected_rows.
    """
    partials = []
    rows = 0
    for frame in batches:
        required = ['txn_date', 'from_bank', 'to_bank', 'payment_currency',
                    'receiving_currency', 'amount_usd']
        if frame[required].isna().any().any():
            raise ValueError('Missing reference corridor value')
        # Strings are required so bank IDs such as '0012' are not silently
        # converted to the number 12 (which would not match Athena's output).
        for column in required[:-1]:
            if not frame[column].map(lambda value: isinstance(value, str)).all():
                raise ValueError('Reference identifiers/dates must be strings')
        if not frame.txn_date.between('2022-09-01', '2022-09-18').all():
            raise ValueError('Reference date outside reviewed scope')
        if not np.isfinite(frame.amount_usd).all() or frame.amount_usd.lt(0).any():
            raise ValueError('Invalid reference USD volume')
        rows += len(frame)
        # Same two GROUP BYs as the SQL's UNION ALL: once on banks, once on
        # currencies, renamed to the shared from_node/to_node columns.
        for kind, source, target in [('bank', 'from_bank', 'to_bank'),
                                     ('currency', 'payment_currency', 'receiving_currency')]:
            grouped = frame.groupby(['txn_date', source, target], observed=True).amount_usd.agg(
                transactions='size', volume_usd='sum').reset_index()
            partials.append(grouped.rename(columns={source: 'from_node', target: 'to_node'}).assign(corridor_type=kind))
    if rows != expected_rows or not partials:
        raise ValueError('Reference population mismatch')
    # A corridor can appear in several batches; sum the partial totals.
    expected = pd.concat(partials, ignore_index=True).groupby(KEYS, observed=True)[
        ['transactions', 'volume_usd']].sum().reset_index()
    # volume_share = corridor volume / total volume of that type on that day.
    # A zero daily total becomes NaN, like SQL's nullif(..., 0) -> NULL.
    totals = expected.groupby(['corridor_type', 'txn_date']).volume_usd.transform('sum')
    expected['volume_share'] = expected.volume_usd / totals.replace(0, np.nan)
    # daily_hhi = sum of squared shares for that type and day, repeated on
    # every row of the group (like the SQL window function). min_count=1
    # keeps it NaN when all shares are NaN, matching SQL NULL.
    expected['daily_hhi'] = expected.volume_share.pow(2).groupby(
        [expected.corridor_type, expected.txn_date]).transform(lambda group: group.sum(min_count=1))
    return expected


def compare_corridors(expected, actual, expected_rows):
    """Compare the pandas reference with Athena's result table.

    Args:
        expected: output of ``expected_corridors``.
        actual: Athena result loaded as a DataFrame (key columns as strings).
        expected_rows: total transaction count each corridor type must sum to.

    Returns:
        A ``status='passed'`` proof dict with row counts, the list of days,
        the largest absolute difference per metric and the tolerances used.

    Raises:
        ValueError: on wrong columns, missing or duplicate keys, a corridor
        present on only one side, any metric outside tolerance, or a
        population total that does not equal expected_rows.

    Tolerances: ``transactions`` must match exactly (integers);
    ``volume_usd`` within 1 cent absolute or 1e-10 relative (float sums in
    a different order differ slightly); shares and HHI within 1e-12.
    """
    if set(actual.columns) != set(KEYS + METRICS) or actual[KEYS].isna().any().any():
        raise ValueError('Unexpected query columns or missing corridor key')
    if actual.duplicated(KEYS).any():
        raise ValueError('Duplicate query corridor')
    # Outer join + indicator shows corridors that exist on only one side.
    merged = expected.merge(actual, on=KEYS, how='outer', suffixes=('_expected', '_actual'), indicator=True)
    if not merged['_merge'].eq('both').all():
        raise ValueError('Missing or unexpected query corridor')
    differences = {}
    for metric in METRICS:
        left = pd.to_numeric(merged[metric + '_expected'], errors='raise').to_numpy(dtype=float)
        right = pd.to_numeric(merged[metric + '_actual'], errors='raise').to_numpy(dtype=float)
        atol = 0.01 if metric == 'volume_usd' else 1e-12
        # equal_nan=True: a NULL share/HHI on both sides counts as equal.
        equal = np.array_equal(left, right) if metric == 'transactions' else np.allclose(left, right, rtol=1e-10, atol=atol, equal_nan=True)
        if not equal:
            raise ValueError('Query metric mismatch: ' + metric)
        finite = np.isfinite(left) & np.isfinite(right)
        differences[metric] = float(np.max(np.abs(left[finite] - right[finite]))) if finite.any() else 0.0
    # Every transaction belongs to exactly one bank corridor and one currency
    # corridor, so each type's counts must add up to the whole population.
    for kind in ['bank', 'currency']:
        if int(actual.loc[actual.corridor_type.eq(kind), 'transactions'].sum()) != expected_rows:
            raise ValueError('Query population mismatch')
    return dict(status='passed', reference_rows=expected_rows, corridor_rows=len(actual),
                days=sorted(actual.txn_date.unique().tolist()), identifiers_preserved=True,
                max_absolute_differences=differences, volume_rtol=1e-10, volume_atol_usd=0.01)
