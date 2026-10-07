"""Local version of the core transaction transformation.

The functions in this module deliberately mirror the Glue curation job while
remaining small enough to run against fixture data or a 1,000-row smoke sample.

Where this sits in the pipeline
-------------------------------
In the cloud, raw Kaggle CSVs (``HI-Small_Trans.csv`` and
``HI-Small_accounts.csv``) plus the historical FX table land in the S3 raw
zone, and the Glue PySpark job ``jobs/curate_transactions.py`` turns them into
curated, ``txn_date``-partitioned Parquet. This module is the pandas twin of
that curation step. It is used to:

* run the whole pipeline on a laptop (``scripts/local/run-local-pipeline.py``);
* build "expected" reference outputs for the AWS smoke tests
  (``scripts/validation/prepare-aws-smoke.py`` and
  ``prepare-ingestion-smoke.py``), so the Glue output can be compared to a
  trusted pandas result row by row;
* share the raw schema constants (``TRANSACTION_COLUMNS``,
  ``IDENTIFIER_DTYPES``, ``ACCOUNT_COLUMN_MAP``) with the ingestion code in
  ``ingest_compute.py`` and ``acquisition.py``.

Inputs: the transaction CSV, the account metadata CSV and an FX table with one
USD rate per (txn_date, currency) produced by ``fx.build_daily_rates``.
Output: a pandas DataFrame (and optionally a local Parquet directory
partitioned by ``txn_date``) with validated types, account/entity metadata for
both sides of each transfer, simple flags and a USD amount used by BQ2.

Note: this local version only computes ``amount_usd`` (same-day rate). The Glue
job additionally computes ``amount_usd_asof`` (previous-day rate) for the model;
the smoke-test scripts derive that column separately.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import numpy as np


# The Kaggle transaction CSV has a header row with two columns literally named
# "Account" (one for the sender, one for the receiver). Reading by header name
# would make pandas rename the second to "Account.1", so instead we discard the
# header and assign these snake_case names *by position*. The order therefore
# must match the raw file column order exactly.
TRANSACTION_COLUMNS = [
    "timestamp",
    "from_bank",
    "from_account",
    "to_bank",
    "to_account",
    "amount_received",
    "receiving_currency",
    "amount_paid",
    "payment_currency",
    "payment_format",
    "is_laundering",
]

# Raw account-file header -> curated column name. Only these five columns are
# kept; anything else in the account file is dropped.
ACCOUNT_COLUMN_MAP = {
    "Bank Name": "bank_name",
    "Bank ID": "bank_id",
    "Account Number": "account_number",
    "Entity ID": "entity_id",
    "Entity Name": "entity_name",
}

# Bank and account identifiers look numeric/hex but are really labels. Reading
# them as strings keeps leading zeroes (e.g. bank "010") and avoids pandas
# converting long hex account numbers into floats.
IDENTIFIER_DTYPES = {
    "from_bank": "string",
    "from_account": "string",
    "to_bank": "string",
    "to_account": "string",
}


def read_transactions(path: str | Path, nrows: int | None = None) -> pd.DataFrame:
    """Read transaction columns by position so duplicate Account headers are safe.

    Args:
        path: Local path to the raw IBM AML transaction CSV
            (for example ``dataset/HI-Small_Trans.csv``).
        nrows: Optional row limit; ``1000`` is used for the smoke run so the
            whole pipeline can be exercised in seconds.

    Returns:
        A DataFrame with ``source_row_index`` (0-based position of the row in
        the source file) followed by the columns in ``TRANSACTION_COLUMNS``.
        ``source_row_index`` is the stable identity used later to build
        ``transaction_id`` values such as ``HI-Small:123`` and to break ties
        when ranking, so it must reflect the original file order.

    Raises:
        ValueError: if the resulting column list is not exactly the expected
            schema.
    """

    # header=0 tells pandas the first line is a header (so it is skipped),
    # while names=... replaces those header names with our positional names.
    frame = pd.read_csv(
        path,
        header=0,
        names=TRANSACTION_COLUMNS,
        dtype=IDENTIFIER_DTYPES,
        nrows=nrows,
    )
    # Defensive check: fail loudly rather than silently mislabel columns.
    if list(frame.columns) != TRANSACTION_COLUMNS:
        raise ValueError("Unexpected transaction schema")

    # Record each row's original position before any sorting/merging happens.
    frame.insert(0, "source_row_index", range(len(frame)))
    return frame


def read_accounts(path: str | Path) -> pd.DataFrame:
    """Read account metadata while preserving leading zeroes in identifiers.

    Args:
        path: Local path to the raw account CSV (``HI-Small_accounts.csv``).

    Returns:
        A DataFrame with exactly the columns ``bank_name, bank_id,
        account_number, entity_id, entity_name`` (all string dtype).

    Raises:
        ValueError: if any expected column is missing, or if the same
            (Bank ID, Account Number) pair appears more than once. Uniqueness
            matters because the account table is joined many-to-one onto
            transactions; a duplicate key would silently duplicate
            transactions and inflate volumes.
    """

    # Every column is read as a string so IDs such as "010" keep their zeroes.
    frame = pd.read_csv(path, dtype="string")
    # Raw headers can carry stray spaces; strip them before matching names.
    frame.columns = frame.columns.str.strip()
    missing = set(ACCOUNT_COLUMN_MAP) - set(frame.columns)
    if missing:
        raise ValueError(f"Account file is missing columns: {sorted(missing)}")

    # Rename to snake_case and keep only the mapped columns, in a fixed order.
    frame = frame.rename(columns=ACCOUNT_COLUMN_MAP)[list(ACCOUNT_COLUMN_MAP.values())]
    # keep=False marks *every* copy of a duplicated key, not just the extras.
    duplicate_keys = frame.duplicated(["bank_id", "account_number"], keep=False)
    if duplicate_keys.any():
        raise ValueError("Account metadata contains duplicate (Bank ID, Account Number) keys")
    return frame


def _validate_transactions(frame: pd.DataFrame) -> None:
    """Data-quality gate applied before any enrichment.

    Checks that every required column exists, that none of them contain nulls,
    and that both amount columns are non-negative and finite (no NaN/inf).
    This mirrors the reject rules in the Glue curation job: bad input stops the
    run instead of producing a partially wrong curated table.

    Args:
        frame: Transactions after type conversion in
            ``build_curated_transactions``.

    Raises:
        ValueError: describing the first failed check.
    """
    # Columns that downstream steps depend on. ``is_laundering`` is validated
    # separately by the caller (it must be 0/1).
    required = {
        "timestamp",
        "from_bank",
        "from_account",
        "to_bank",
        "to_account",
        "amount_received",
        "amount_paid",
        "payment_currency",
        "receiving_currency",
        "payment_format",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Transactions are missing columns: {sorted(missing)}")

    # .any().any(): first per column, then across all columns.
    if frame[list(required)].isna().any().any():
        raise ValueError("Required transaction fields contain null values")
    # Negative money amounts are impossible for this dataset and would also
    # break the log1p() transform used by the model later on.
    if (frame[["amount_received", "amount_paid"]] < 0).any().any():
        raise ValueError("Transaction amounts must be non-negative")
    if not np.isfinite(frame[["amount_received", "amount_paid"]]).all().all():
        raise ValueError('Transaction amounts must be finite')


def _attach_account_metadata(
    transactions: pd.DataFrame, accounts: pd.DataFrame
) -> pd.DataFrame:
    """Join bank name and entity details onto both the sender and receiver.

    The account table is joined twice: once on (from_bank, from_account) to
    add ``from_bank_name``, ``from_entity_id``, ``from_entity_name``; and once
    on (to_bank, to_account) to add the matching ``to_*`` columns.

    Args:
        transactions: Validated transactions.
        accounts: Output of ``read_accounts``.

    Returns:
        The transactions with six extra metadata columns. The join is a LEFT
        join, so transactions whose account is not in the metadata keep their
        row with null metadata (the Glue job is stricter and rejects those).

    Raises:
        pandas.errors.MergeError: via ``validate="many_to_one"`` if the account
            side unexpectedly has duplicate keys (which would duplicate rows).
    """
    metadata = ["bank_name", "entity_id", "entity_name"]
    result = transactions

    for side in ("from", "to"):
        # Transactions pad numeric bank IDs (e.g. 010); account metadata uses 10.
        # Normalize only the join key, preserving the original source identifiers.
        bank_key = f"_{side}_bank_key"
        # Strip leading zeroes; an all-zero ID ("000") would become "" so map it
        # back to "0" so that bank 0 still matches.
        normalized_bank = (
            result[f"{side}_bank"].astype("string").str.lstrip("0").replace("", "0")
        )
        # Add the temporary join key as a new column (original bank untouched).
        result = result.assign(**{bank_key: normalized_bank})
        # Rename the account columns so they line up with this side's names,
        # e.g. account_number -> from_account and bank_name -> from_bank_name.
        renamed = accounts.rename(
            columns={
                "bank_id": bank_key,
                "account_number": f"{side}_account",
                **{column: f"{side}_{column}" for column in metadata},
            }
        )
        # Apply the same zero-stripping to the metadata side of the key.
        renamed[bank_key] = (
            renamed[bank_key].astype("string").str.lstrip("0").replace("", "0")
        )
        # many_to_one: many transactions may share one account, but each
        # account key must appear only once in the metadata.
        result = result.merge(
            renamed,
            on=[bank_key, f"{side}_account"],
            how="left",
            validate="many_to_one",
        ).drop(columns=[bank_key])
    return result


def _attach_usd_amounts(
    transactions: pd.DataFrame, fx_rates: pd.DataFrame
) -> pd.DataFrame:
    """Convert each transaction's paid amount to US dollars.

    Formula: ``amount_usd = amount_paid * usd_rate`` where ``usd_rate`` is the
    number of USD per one unit of the *payment* currency on the transaction's
    calendar date (``txn_date``). US Dollar payments always use a rate of 1.0.
    A common USD unit is what lets BQ2 add up corridor volumes across
    currencies.

    Args:
        transactions: Transactions with a ``txn_date`` column (a ``date``).
        fx_rates: Table with at least ``txn_date``, ``currency`` (IBM currency
            name such as "Euro") and ``usd_rate``; normally the CSV written by
            ``fx.write_rates_csv``.

    Returns:
        The transactions plus a float ``amount_usd`` column. The helper
        columns ``currency`` and ``usd_rate`` from the join are dropped.

    Raises:
        ValueError: if the FX table is missing columns, has non-positive or
            non-finite rates, has duplicate (date, currency) rows, or lacks a
            rate for any non-USD currency/date that appears in the data.
    """
    required = {"txn_date", "currency", "usd_rate"}
    missing = required - set(fx_rates.columns)
    if missing:
        raise ValueError(f"FX rates are missing columns: {sorted(missing)}")

    # Work on a copy so the caller's FX table is not modified in place.
    rates = fx_rates.copy()
    # Normalise the join key types to match transactions.txn_date (python date)
    # and string currency names; errors="raise" makes bad values fail loudly.
    rates["txn_date"] = pd.to_datetime(rates["txn_date"], errors="raise").dt.date
    rates["currency"] = rates["currency"].astype("string")
    rates['usd_rate'] = pd.to_numeric(rates.usd_rate, errors='raise')
    # A zero/negative/inf rate would produce meaningless USD volumes.
    if not (np.isfinite(rates.usd_rate) & rates.usd_rate.gt(0)).all():
        raise ValueError('FX rates must be positive and finite')
    # Two rates for the same day/currency would duplicate transactions on join.
    if rates.duplicated(["txn_date", "currency"]).any():
        raise ValueError("FX rates must be unique by (txn_date, currency)")

    # Match on the *payment* currency: amount_paid is what left the sender.
    result = transactions.merge(
        rates[["txn_date", "currency", "usd_rate"]],
        left_on=["txn_date", "payment_currency"],
        right_on=["txn_date", "currency"],
        how="left",
        validate="many_to_one",
    )
    # USD needs no lookup; force the identity rate even if the FX file has none.
    is_usd = result["payment_currency"].eq("US Dollar")
    result.loc[is_usd, "usd_rate"] = 1.0

    # Any remaining null rate means the FX file does not cover that currency
    # on that date; report the offending currencies instead of emitting NaN.
    missing_rates = result.loc[~is_usd & result["usd_rate"].isna(), "payment_currency"]
    if not missing_rates.empty:
        currencies = sorted(missing_rates.dropna().unique().tolist())
        raise ValueError(f"Missing FX rates for currencies: {currencies}")

    result["amount_usd"] = result["amount_paid"] * result["usd_rate"]
    return result.drop(columns=["currency", "usd_rate"])


def build_curated_transactions(
    transactions: pd.DataFrame,
    accounts: pd.DataFrame,
    fx_rates: pd.DataFrame,
) -> pd.DataFrame:
    """Validate, enrich, and feature-engineer raw transactions.

    This is the local equivalent of the Glue curation job. Steps, in order:

    1. Parse types (timestamp, numeric amounts) and check the label is 0/1.
    2. Run ``_validate_transactions`` (nulls, negative or infinite amounts).
    3. Derive ``txn_date`` (calendar date used for partitioning and FX lookup),
       ``is_self_txn`` (money sent to the very same bank+account) and
       ``currency_mismatch`` (paid and received currencies differ, i.e. a
       cross-currency transfer). Both flags are simple AML signals.
    4. Attach sender/receiver account metadata.
    5. Convert ``amount_paid`` to ``amount_usd``.

    Args:
        transactions: Output of ``read_transactions``.
        accounts: Output of ``read_accounts``.
        fx_rates: Daily FX table (see ``fx.build_daily_rates``).

    Returns:
        A new curated DataFrame; the input frame is not modified.

    Raises:
        ValueError: on any parse or validation failure (no partial output).
    """

    # Copy first so that type conversion does not mutate the caller's data.
    result = transactions.copy()
    # errors="raise": a single unparseable value aborts the run.
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="raise")
    result["amount_received"] = pd.to_numeric(result["amount_received"], errors="raise")
    result["amount_paid"] = pd.to_numeric(result["amount_paid"], errors="raise")
    # The label is only used for evaluation, never as a model input, but it
    # still has to be a clean binary flag.
    labels = pd.to_numeric(result['is_laundering'], errors='raise')
    if not labels.isin([0, 1]).all():
        raise ValueError('is_laundering must contain only 0 or 1')
    # int8 keeps the column small when the full dataset is loaded.
    result['is_laundering'] = labels.astype('int8')
    _validate_transactions(result)

    # Calendar date (no time) - partition key and FX join key.
    result["txn_date"] = result["timestamp"].dt.date
    # Self-transfer: same bank AND same account on both sides. Note that this
    # compares the raw bank strings (the model's encoder uses zero-stripped
    # bank IDs for the same flag).
    result["is_self_txn"] = result["from_bank"].eq(result["to_bank"]) & result[
        "from_account"
    ].eq(result["to_account"])
    # True when the payer's currency differs from the receiver's currency.
    result["currency_mismatch"] = ~result["receiving_currency"].eq(
        result["payment_currency"]
    )

    result = _attach_account_metadata(result, accounts)
    return _attach_usd_amounts(result, fx_rates)


def write_partitioned_parquet(frame: pd.DataFrame, output_dir: str | Path) -> None:
    """Write the curated table using the txn_date partition.

    Produces a Hive-style folder layout such as
    ``output_dir/txn_date=2022-09-01/<file>.parquet``, the same layout the Glue
    job writes to the curated S3 zone. Partitioning by date lets Athena and
    pyarrow skip whole days when a query filters on ``txn_date``.

    Args:
        frame: Output of ``build_curated_transactions``.
        output_dir: Local directory to write into (created by pyarrow).

    Side effects:
        Writes Parquet files to disk; the DataFrame index is not stored.
    """

    frame.to_parquet(output_dir, engine="pyarrow", partition_cols=["txn_date"], index=False)
