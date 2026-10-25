"""Treasury corridor volume and concentration (BQ2).

* ``corridor_totals`` sums USD volume per day and directed bank pair /
  currency pair ("corridor").
* ``concentration`` turns daily corridor volumes into an HHI and top-5 share.

``control.export`` calls these chunk by chunk over the full curated dataset.
"""
import pandas as pd

from ..features.behaviour import bank_key


def corridor_totals(frame):
    """Aggregate USD volume per daily corridor (BQ2).

    A corridor is a directed route: sending bank -> receiving bank, together
    with the payment and receiving currencies. Bank IDs are zero-stripped first
    so ``"010"`` and ``"10"`` fall into the same corridor. This is the pandas
    equivalent of the Athena corridor SQL.

    Args:
        frame: Curated transactions with ``txn_date`` and ``amount_usd``.

    Returns:
        One row per (txn_date, from_bank, to_bank, payment_currency,
        receiving_currency) with ``transaction_count`` and ``volume_usd``.
        Because batches can be aggregated separately and summed later,
        ``batch.py`` calls this chunk by chunk.
    """
    frame = frame.assign(from_bank=bank_key(frame.from_bank), to_bank=bank_key(frame.to_bank))
    keys = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency']
    # observed=True: if any key is categorical, skip empty combinations.
    return frame.groupby(keys, observed=True).agg(
        transaction_count=('amount_usd', 'size'), volume_usd=('amount_usd', 'sum')).reset_index()


def concentration(daily):
    """Daily directed bank-pair HHI (0..1), with currencies summed first.

    The Herfindahl-Hirschman Index measures how concentrated a market is:
    ``HHI = sum(share_i ** 2)`` where ``share_i`` is bank pair i's fraction of
    that day's total USD volume. HHI near 0 means volume is spread over many
    bank pairs; HHI = 1 means one bank pair carries everything. ``top5_share``
    is the combined share of the five largest bank pairs - an easier number to
    explain to treasury.

    Args:
        daily: Output of ``corridor_totals`` (one row per corridor and day).

    Returns:
        DataFrame with ``txn_date, volume_usd, hhi, top5_share`` per day.
        If a day's total volume is 0, ``hhi`` and ``top5_share`` are ``None``
        (shares would be undefined).
    """
    # Collapse currency pairs so each directed bank pair is one "player".
    bank = daily.groupby(['txn_date', 'from_bank', 'to_bank']).volume_usd.sum().reset_index()
    rows = []
    for day, group in bank.groupby('txn_date'):
        total = group.volume_usd.sum()
        shares = group.volume_usd / total if total > 0 else None
        rows.append(dict(txn_date=day, volume_usd=float(total),
                         hhi=float((shares**2).sum()) if shares is not None else None,
                         top5_share=float(shares.nlargest(5).sum()) if shares is not None else None))
    return pd.DataFrame(rows)
