import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from bda_pipeline.analytics import add_behaviour, corridor_totals

SPEC = importlib.util.spec_from_file_location("verify_full", Path(__file__).resolve().parents[1] / "scripts/verify-aws-full.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_full_verification_checks_ids_features_and_corridors(tmp_path):
    frame = pd.DataFrame({
        "source_row_index": [0, 1], "transaction_id": ["HI-Small:0", "HI-Small:1"],
        "timestamp": pd.to_datetime(["2022-09-01 00:00", "2022-09-01 01:00"]),
        "txn_date": ["2022-09-01"] * 2,
        "from_bank": ["020"] * 2, "from_account": ["0001"] * 2,
        "to_bank": ["021"] * 2, "to_account": ["0002"] * 2,
        "payment_currency": ["US Dollar"] * 2, "receiving_currency": ["US Dollar"] * 2,
        "from_entity_id": ["E1"] * 2, "to_entity_id": ["E2"] * 2,
        "amount_usd": [10., 20.], "amount_usd_asof": [10., 20.], "is_laundering": [0, 1],
    })
    frame = add_behaviour(frame)
    cloud, reference, corridors = [tmp_path / name for name in ["cloud.parquet", "reference.parquet", "corridors.parquet"]]
    frame.to_parquet(cloud, index=False)
    frame.to_parquet(reference, index=False)
    corridor_totals(frame).to_parquet(corridors, index=False)
    assert MODULE.verify(cloud, reference, corridors, 2)["status"] == "passed"
    frame.loc[1, "txn_count_1h"] = 99
    frame.to_parquet(cloud, index=False)
    with pytest.raises(AssertionError):
        MODULE.verify(cloud, reference, corridors, 2)
    frame.loc[1, "source_row_index"] = 0
    frame.to_parquet(cloud, index=False)
    with pytest.raises(ValueError, match="Duplicate"):
        MODULE.verify(cloud, reference, corridors, 2)
