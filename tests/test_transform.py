from __future__ import annotations

import pandas as pd
import pytest

from bda_pipeline.transform import (
    build_curated_transactions,
    read_accounts,
    read_transactions,
    write_partitioned_parquet,
)


def test_read_transactions_handles_duplicate_account_headers(tmp_path):
    path = tmp_path / "transactions.csv"
    path.write_text(
        "Timestamp,From Bank,Account,To Bank,Account,Amount Received,"
        "Receiving Currency,Amount Paid,Payment Currency,Payment Format,Is Laundering\n"
        "2022/09/01 00:00,020,0007,021,0008,10,US Dollar,10,US Dollar,ACH,1\n",
        encoding="utf-8",
    )

    frame = read_transactions(path)

    assert frame.loc[0, "from_account"] == "0007"
    assert frame.loc[0, "to_account"] == "0008"
    assert frame.loc[0, "from_bank"] == "020"


def test_read_accounts_rejects_duplicate_composite_keys(tmp_path):
    path = tmp_path / "accounts.csv"
    path.write_text(
        "Bank Name,Bank ID,Account Number,Entity ID,Entity Name\n"
        "A,020,0007,E1,Person\n"
        "A,020,0007,E2,Business\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        read_accounts(path)


def test_build_curated_uses_composite_account_key_and_fx_rate():
    transactions = pd.DataFrame(
        {
            "source_row_index": [0, 1],
            "timestamp": ["2022-09-01 10:00", "2022-09-01 11:00"],
            "from_bank": pd.Series(["020", "021"], dtype="string"),
            "from_account": pd.Series(["0007", "0007"], dtype="string"),
            "to_bank": pd.Series(["021", "021"], dtype="string"),
            "to_account": pd.Series(["0007", "0007"], dtype="string"),
            "amount_received": [90.0, 20.0],
            "receiving_currency": ["US Dollar", "US Dollar"],
            "amount_paid": [100.0, 20.0],
            "payment_currency": ["Euro", "US Dollar"],
            "payment_format": ["ACH", "Cheque"],
            "is_laundering": [1, 0],
        }
    )
    accounts = pd.DataFrame(
        {
            "bank_name": ["Bank A", "Bank B"],
            "bank_id": pd.Series(["20", "21"], dtype="string"),
            "account_number": pd.Series(["0007", "0007"], dtype="string"),
            "entity_id": ["E-A", "E-B"],
            "entity_name": ["Alice", "Bob"],
        }
    )
    fx_rates = pd.DataFrame(
        {"txn_date": ["2022-09-01"], "currency": ["Euro"], "usd_rate": [1.1]}
    )

    curated = build_curated_transactions(transactions, accounts, fx_rates)

    assert curated["from_entity_id"].tolist() == ["E-A", "E-B"]
    assert curated["to_entity_id"].tolist() == ["E-B", "E-B"]
    assert curated["from_bank_name"].tolist() == ["Bank A", "Bank B"]
    assert curated["to_bank_name"].tolist() == ["Bank B", "Bank B"]
    assert curated["from_bank"].tolist() == ["020", "021"]
    assert curated["from_account"].tolist() == ["0007", "0007"]
    assert curated["amount_usd"].tolist() == pytest.approx([110.0, 20.0])
    assert curated["is_self_txn"].tolist() == [False, True]
    assert curated["currency_mismatch"].tolist() == [True, False]


def test_build_curated_rejects_missing_non_usd_fx_rate():
    transactions = pd.DataFrame(
        {
            "timestamp": ["2022-09-01"],
            "from_bank": pd.Series(["020"], dtype="string"),
            "from_account": pd.Series(["0007"], dtype="string"),
            "to_bank": pd.Series(["021"], dtype="string"),
            "to_account": pd.Series(["0008"], dtype="string"),
            "amount_received": [10],
            "receiving_currency": ["Euro"],
            "amount_paid": [10],
            "payment_currency": ["Euro"],
            "payment_format": ["ACH"],
            "is_laundering": [0],
        }
    )
    accounts = pd.DataFrame(
        columns=["bank_name", "bank_id", "account_number", "entity_id", "entity_name"]
    )
    fx_rates = pd.DataFrame(columns=["txn_date", "currency", "usd_rate"])

    with pytest.raises(ValueError, match="Missing FX rates"):
        build_curated_transactions(transactions, accounts, fx_rates)


def test_write_partitioned_parquet_creates_date_partition(tmp_path):
    frame = pd.DataFrame(
        {"txn_date": ["2022-09-01"], "transaction_id": [1], "amount_usd": [10.0]}
    )

    write_partitioned_parquet(frame, tmp_path / "curated")

    partitions = [path.name for path in (tmp_path / "curated").iterdir()]
    assert partitions == ["txn_date=2022-09-01"]
