"""Causal per-sender behaviour features (BQ1). Labels are never used here.

``FEATURES`` names the five velocity / fan-out counts that the Glue curate job
(``jobs/curate_transactions.py``) writes for every transaction and that the
rule baseline, the XGBoost model and the streaming Lambda all read.
``add_behaviour`` is the pandas reference implementation of those counts over
rolling 1-hour, 24-hour and 7-day windows; ``bank_key`` is the bank-ID
normalisation shared with the corridor aggregation.

Who calls it:
    * ``control.ingest_compute`` and the validation scripts call
      ``add_behaviour`` to recompute the Glue-produced features as a
      cross-check.
    * ``modeling.model``, ``scoring.inference`` and ``scoring.streaming`` use
      ``FEATURES`` as the model's behaviour-feature contract.
    * ``treasury.corridors`` uses ``bank_key``.
"""
from collections import Counter, deque

import numpy as np
import pandas as pd


# Causal behaviour features computed by the curate job for each sender account:
# number of outgoing transactions in the previous 1h / 24h / 7 days and the
# number of distinct receiving accounts in the previous 24h / 7 days. They only
# look backwards in time, so they never leak future information into scoring.
FEATURES = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
            'distinct_counterparties_24h', 'distinct_counterparties_7d']


def bank_key(values):
    """Normalise bank IDs for comparison by stripping leading zeroes.

    The raw transaction file writes some bank IDs zero-padded (``"010"``)
    while other sources write ``"10"``; both must be treated as the same bank.
    An all-zero ID would become an empty string, so it is mapped back to
    ``"0"``.

    Args:
        values: A pandas Series of bank identifiers (any dtype).

    Returns:
        A pandas ``string`` Series of normalised bank IDs.
    """
    return values.astype('string').str.lstrip('0').replace('', '0')


def add_behaviour(frame):
    """Exact outgoing windows [t-window,t); same-timestamp peers are excluded.

    Input must contain complete outgoing history for each included sender.
    History before the dataset starts is unavailable (cold start).

    For every transaction, looks back over the *same sender's* earlier
    outgoing transactions and counts:

    * ``txn_count_1h`` / ``txn_count_24h`` / ``txn_count_7d`` - how many
      payments the sender made in the previous 1 hour, 24 hours and 7 days
      (velocity: bursts of activity are a classic laundering signal);
    * ``distinct_counterparties_24h`` / ``distinct_counterparties_7d`` - how
      many different receiving (bank, account) pairs the sender paid in the
      previous 24 hours and 7 days (fan-out: spreading money across many
      accounts).

    "Previous" is strict: the window is [t - window, t), so the current
    transaction and any other transactions with the *identical* timestamp are
    not counted. This avoids leaking information from simultaneous rows whose
    order in the file is arbitrary.

    Algorithm: a sliding window per sender. Rows are visited in time order;
    each window keeps a deque of (timestamp, counterparty) entries plus a
    Counter of counterparties. Old entries are popped from the left as the
    window moves, so each row is pushed and popped at most once per window
    (roughly linear time per sender instead of quadratic).

    Args:
        frame: Transactions with ``from_bank, from_account, to_bank,
            to_account, timestamp, source_row_index``.

    Returns:
        A copy of ``frame`` (index reset to 0..n-1) with the five int64
        feature columns added.
    """
    # reset_index so row labels are 0..n-1 and can be used to index `features`.
    result = frame.copy().reset_index(drop=True)
    # Zero-stripped bank IDs so "010" and "10" are grouped as the same bank.
    result['_bank'] = bank_key(result.from_bank)
    result['_to_bank'] = bank_key(result.to_bank)
    columns = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
               'distinct_counterparties_24h', 'distinct_counterparties_7d']
    # One row of 5 feature values per transaction, filled in below.
    features = np.zeros((len(result), 5), dtype=np.int64)
    # A "sender" is a (bank, account) pair; windows are computed independently.
    for _, group in result.groupby(['_bank', 'from_account'], sort=False):
        # source_row_index breaks timestamp ties deterministically.
        group = group.sort_values(['timestamp', 'source_row_index'])
        # Index 0/1/2 = the 1h / 24h / 7d windows respectively.
        queues = [deque(), deque(), deque()]
        counters = [Counter(), Counter(), Counter()]
        records = list(zip(group.index, group.timestamp, group._to_bank, group.to_account))
        pos = 0
        while pos < len(records):
            # Find the block [pos, end) of rows that share the same timestamp;
            # they are processed together so none of them sees the others.
            end = pos + 1
            now = records[pos][1]
            while end < len(records) and records[end][1] == now:
                end += 1
            # Slide each window: drop entries older than now - window
            # (168 hours = 7 days). Entries exactly at the cutoff are kept,
            # which makes the lower bound inclusive.
            for q, counts, hours in zip(queues, counters, [1, 24, 168]):
                cutoff = now - pd.Timedelta(hours=hours)
                while q and q[0][0] < cutoff:
                    _, party = q.popleft()
                    counts[party] -= 1
                    # Remove zero entries so len(counts) = distinct parties.
                    if counts[party] == 0:
                        del counts[party]
            # Record features for every row in the same-timestamp block BEFORE
            # adding the block to the windows (strict "earlier only" rule).
            # Counts = deque lengths; distinct = keys in the 24h and 7d Counters.
            for idx, _, _, _ in records[pos:end]:
                features[idx] = [len(q) for q in queues] + [len(counters[1]), len(counters[2])]
            # Now add the block so later rows can see it.
            for _, timestamp, bank, account in records[pos:end]:
                for q, counts in zip(queues, counters):
                    q.append((timestamp, (bank, account)))
                    counts[(bank, account)] += 1
            pos = end
    result[columns] = features
    return result.drop(columns=['_bank', '_to_bank'])
