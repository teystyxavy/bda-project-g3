import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validation" / "prepare-aws-smoke.py"
SPEC = importlib.util.spec_from_file_location("prepare_aws_smoke", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_smoke_preserves_raw_header_identifiers_and_both_fx_dates(tmp_path):
    transactions = tmp_path / "input.csv"
    transactions.write_text(
        "Timestamp,From Bank,Account,To Bank,Account,Amount Received,Receiving Currency,Amount Paid,Payment Currency,Payment Format,Is Laundering\n"
        "2022/09/01 00:00,020,0007,021,0007,10,Euro,10,Euro,ACH,1\n"
        "2022/09/01 01:00,020,0007,021,0007,20,Euro,20,Euro,ACH,0\n",
        encoding="utf-8",
    )
    accounts = tmp_path / "accounts.csv"
    accounts.write_text(
        "Bank Name,Bank ID,Account Number,Entity ID,Entity Name\n"
        "A,20,0007,E1,Alice\nB,21,0007,E2,Bob\nC,22,0008,E3,Unused\n", encoding="utf-8",
    )
    fx = tmp_path / "fx.csv"
    fx.write_text("txn_date,currency,usd_rate\n2022-08-31,Euro,1.1\n2022-09-01,Euro,1.2\n", encoding="utf-8")
    output = tmp_path / "sample"
    summary = MODULE.prepare(transactions, accounts, fx, output, 1)
    assert summary["rows"] == 1
    assert summary["amount_usd"] == pytest.approx(12)
    assert summary["amount_usd_asof"] == pytest.approx(11)
    assert (output / "transactions.csv").read_text().splitlines()[0].count("Account") == 2
    assert len(pd.read_csv(output / "accounts.csv")) == 2
    expected = pd.read_parquet(output / "expected.parquet")
    assert expected.from_bank.iloc[0] == "020"
    assert expected.from_entity_id.iloc[0] == "E1"
    assert expected.to_entity_id.iloc[0] == "E2"
    assert json.loads((output / "expected.json").read_text())["positives"] == 1
    with pytest.raises(FileExistsError):
        MODULE.prepare(transactions, accounts, fx, output, 1)
