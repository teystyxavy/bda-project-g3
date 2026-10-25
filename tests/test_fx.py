from __future__ import annotations

from datetime import date

import pytest

from bda_pipeline.ingestion.fx import build_daily_rates, discover_requirements


def test_discover_requirements_reads_currency_names_and_dates(tmp_path):
    transactions = tmp_path / "transactions.csv"
    transactions.write_text(
        "Timestamp,From Bank,Account,To Bank,Account,Amount Received,"
        "Receiving Currency,Amount Paid,Payment Currency,Payment Format,Is Laundering\n"
        "2022/09/01 10:00,001,A,002,B,10,Euro,10,Euro,ACH,0\n"
        "2022/09/03 10:00,001,A,002,B,10,US Dollar,10,US Dollar,ACH,0\n",
        encoding="utf-8",
    )

    start, end, currencies = discover_requirements(transactions)

    assert (start, end) == (date(2022, 9, 1), date(2022, 9, 3))
    assert currencies == ["Euro", "US Dollar"]


def test_build_daily_rates_inverts_frankfurter_and_carries_weekend_rate():
    def fake_getter(url, params):
        assert params["base"] == "USD"
        return [
            {"date": "2022-09-02", "base": "USD", "quote": "EUR", "rate": 0.8}
        ]

    rates = build_daily_rates(
        ["Euro", "US Dollar"],
        date(2022, 9, 2),
        date(2022, 9, 4),
        getter=fake_getter,
    )

    euro = rates.loc[rates["currency"].eq("Euro")]
    assert euro["usd_rate"].tolist() == pytest.approx([1.25, 1.25, 1.25])
    assert euro["rate_date"].tolist() == [date(2022, 9, 2)] * 3
    assert rates.loc[rates["currency"].eq("US Dollar"), "usd_rate"].eq(1).all()


def test_build_daily_rates_uses_yahoo_for_bitcoin():
    def fake_getter(url, params):
        return {
            "chart": {
                "result": [
                    {
                        "timestamp": [1661990400, 1662076800],
                        "indicators": {"quote": [{"close": [20_000.0, 20_500.0]}]},
                    }
                ]
            }
        }

    rates = build_daily_rates(
        ["Bitcoin"],
        date(2022, 9, 1),
        date(2022, 9, 2),
        getter=fake_getter,
    )

    assert rates["usd_rate"].tolist() == [20_000.0, 20_500.0]
    assert rates["source"].unique().tolist() == ["Yahoo Finance daily close"]


def test_unknown_dataset_currency_is_rejected():
    with pytest.raises(ValueError, match="Unsupported IBM currency"):
        build_daily_rates(["Martian Credits"], date(2022, 9, 1), date(2022, 9, 1))
