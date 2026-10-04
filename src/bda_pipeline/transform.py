"""Local version of the core transaction transformation.

The functions in this module deliberately mirror the planned Glue job while
remaining small enough to run against fixture data or a 1,000-row smoke sample.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import numpy as np


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

ACCOUNT_COLUMN_MAP = {
    "Bank Name": "bank_name",
    "Bank ID": "bank_id",
    "Account Number": "account_number",
    "Entity ID": "entity_id",
    "Entity Name": "entity_name",
}

IDENTIFIER_DTYPES = {
    "from_bank": "string",
    "from_account": "string",
    "to_bank": "string",
    "to_account": "string",
}


def read_transactions(path: str | Path, nrows: int | None = None) -> pd.DataFrame:
    """Read transaction columns by position so duplicate Account headers are safe."""

    frame = pd.read_csv(
        path,
        header=0,
        names=TRANSACTION_COLUMNS,
        dtype=IDENTIFIER_DTYPES,
        nrows=nrows,
    )
    if list(frame.columns) != TRANSACTION_COLUMNS:
        raise ValueError("Unexpected transaction schema")

    frame.insert(0, "source_row_index", range(len(frame)))
    return frame


def read_accounts(path: str | Path) -> pd.DataFrame:
    """Read account metadata while preserving leading zeroes in identifiers."""

    frame = pd.read_csv(path, dtype="string")
    frame.columns = frame.columns.str.strip()
    missing = set(ACCOUNT_COLUMN_MAP) - set(frame.columns)
    if missing:
        raise ValueError(f"Account file is missing columns: {sorted(missing)}")

    frame = frame.rename(columns=ACCOUNT_COLUMN_MAP)[list(ACCOUNT_COLUMN_MAP.values())]
    duplicate_keys = frame.duplicated(["bank_id", "account_number"], keep=False)
    if duplicate_keys.any():
        raise ValueError("Account metadata contains duplicate (Bank ID, Account Number) keys")
    return frame


def _validate_transactions(frame: pd.DataFrame) -> None:
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

    if frame[list(required)].isna().any().any():
        raise ValueError("Required transaction fields contain null values")
    if (frame[["amount_received", "amount_paid"]] < 0).any().any():
        raise ValueError("Transaction amounts must be non-negative")
    if not np.isfinite(frame[["amount_received", "amount_paid"]]).all().all():
        raise ValueError('Transaction amounts must be finite')


def _attach_account_metadata(
    transactions: pd.DataFrame, accounts: pd.DataFrame
) -> pd.DataFrame:
    metadata = ["bank_name", "entity_id", "entity_name"]
    result = transactions

    for side in ("from", "to"):
        # Transactions pad numeric bank IDs (e.g. 010); account metadata uses 10.
        # Normalize only the join key, preserving the original source identifiers.
        bank_key = f"_{side}_bank_key"
        normalized_bank = (
            result[f"{side}_bank"].astype("string").str.lstrip("0").replace("", "0")
        )
        result = result.assign(**{bank_key: normalized_bank})
        renamed = accounts.rename(
            columns={
                "bank_id": bank_key,
                "account_number": f"{side}_account",
                **{column: f"{side}_{column}" for column in metadata},
            }
        )
        renamed[bank_key] = (
            renamed[bank_key].astype("string").str.lstrip("0").replace("", "0")
        )
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
    required = {"txn_date", "currency", "usd_rate"}
    missing = required - set(fx_rates.columns)
    if missing:
        raise ValueError(f"FX rates are missing columns: {sorted(missing)}")

    rates = fx_rates.copy()
    rates["txn_date"] = pd.to_datetime(rates["txn_date"], errors="raise").dt.date
    rates["currency"] = rates["currency"].astype("string")
    rates['usd_rate'] = pd.to_numeric(rates.usd_rate, errors='raise')
    if not (np.isfinite(rates.usd_rate) & rates.usd_rate.gt(0)).all():
        raise ValueError('FX rates must be positive and finite')
    if rates.duplicated(["txn_date", "currency"]).any():
        raise ValueError("FX rates must be unique by (txn_date, currency)")

    result = transactions.merge(
        rates[["txn_date", "currency", "usd_rate"]],
        left_on=["txn_date", "payment_currency"],
        right_on=["txn_date", "currency"],
        how="left",
        validate="many_to_one",
    )
    is_usd = result["payment_currency"].eq("US Dollar")
    result.loc[is_usd, "usd_rate"] = 1.0

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
    """Validate, enrich, and feature-engineer raw transactions."""

    result = transactions.copy()
    result["timestamp"] = pd.to_datetime(result["timestamp"], errors="raise")
    result["amount_received"] = pd.to_numeric(result["amount_received"], errors="raise")
    result["amount_paid"] = pd.to_numeric(result["amount_paid"], errors="raise")
    labels = pd.to_numeric(result['is_laundering'], errors='raise')
    if not labels.isin([0, 1]).all():
        raise ValueError('is_laundering must contain only 0 or 1')
    result['is_laundering'] = labels.astype('int8')
    _validate_transactions(result)

    result["txn_date"] = result["timestamp"].dt.date
    result["is_self_txn"] = result["from_bank"].eq(result["to_bank"]) & result[
        "from_account"
    ].eq(result["to_account"])
    result["currency_mismatch"] = ~result["receiving_currency"].eq(
        result["payment_currency"]
    )

    result = _attach_account_metadata(result, accounts)
    return _attach_usd_amounts(result, fx_rates)


def write_partitioned_parquet(frame: pd.DataFrame, output_dir: str | Path) -> None:
    """Write the curated table using the planned txn_date partition."""

    frame.to_parquet(output_dir, engine="pyarrow", partition_cols=["txn_date"], index=False)
