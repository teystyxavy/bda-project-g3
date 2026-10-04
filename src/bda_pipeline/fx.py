"""Historical FX ingestion for the IBM AML transaction currencies."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


# Currency names are the exact values used by the IBM AML dataset.
CURRENCY_CODES = {
    "Australian Dollar": "AUD",
    "Bitcoin": "BTC",
    "Brazil Real": "BRL",
    "Canadian Dollar": "CAD",
    "Euro": "EUR",
    "Mexican Peso": "MXN",
    "Ruble": "RUB",
    "Rupee": "INR",
    "Saudi Riyal": "SAR",
    "Shekel": "ILS",
    "Swiss Franc": "CHF",
    "UK Pound": "GBP",
    "US Dollar": "USD",
    "Yen": "JPY",
    "Yuan": "CNY",
}

# Frankfurter has no queryable September 2022 history for these pairs.
YAHOO_ONLY_CODES = {"BTC", "RUB", "SAR"}

FRANKFURTER_RATES_URL = "https://api.frankfurter.dev/v2/rates"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

JsonGetter = Callable[[str, dict[str, str]], object]


def get_json(url: str, params: dict[str, str], attempts: int = 3) -> object:
    """Fetch JSON with bounded retries and a descriptive user agent."""

    request_url = f"{url}?{urlencode(params)}" if params else url
    request = Request(
        request_url,
        headers={"User-Agent": "IS459-BDA-Group3-FX-Ingestion/0.1"},
    )
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as error:
            last_error = error
            if attempt < attempts - 1:
                time.sleep(2**attempt)
    raise RuntimeError(f"Could not fetch {request_url}: {last_error}") from last_error


def discover_requirements(
    transactions_path: str | Path,
    nrows: int | None = None,
    chunksize: int = 250_000,
) -> tuple[date, date, list[str]]:
    """Find the transaction date range and payment currencies without loading all rows."""

    earliest: date | None = None
    latest: date | None = None
    currencies: set[str] = set()
    rows_remaining = nrows

    reader = pd.read_csv(
        transactions_path,
        usecols=["Timestamp", "Payment Currency"],
        dtype={"Payment Currency": "string"},
        chunksize=chunksize,
    )
    for chunk in reader:
        if rows_remaining is not None:
            if rows_remaining <= 0:
                break
            chunk = chunk.iloc[:rows_remaining]
            rows_remaining -= len(chunk)

        timestamps = pd.to_datetime(chunk["Timestamp"], errors="raise")
        chunk_earliest = timestamps.min().date()
        chunk_latest = timestamps.max().date()
        earliest = chunk_earliest if earliest is None else min(earliest, chunk_earliest)
        latest = chunk_latest if latest is None else max(latest, chunk_latest)
        currencies.update(chunk["Payment Currency"].dropna().astype(str).unique())

    if earliest is None or latest is None:
        raise ValueError("The transaction file contains no rows")

    unknown = currencies - set(CURRENCY_CODES)
    if unknown:
        raise ValueError(f"Unsupported IBM currency names: {sorted(unknown)}")
    return earliest, latest, sorted(currencies)


def _frankfurter_observations(
    codes: Iterable[str],
    start_date: date,
    end_date: date,
    getter: JsonGetter,
) -> pd.DataFrame:
    codes = sorted(set(codes) - {"USD"} - YAHOO_ONLY_CODES)
    if not codes:
        return pd.DataFrame(columns=["rate_date", "iso_code", "usd_rate", "source"])

    payload = getter(
        FRANKFURTER_RATES_URL,
        {
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
            "base": "USD",
            "quotes": ",".join(codes),
        },
    )
    if not isinstance(payload, list):
        raise ValueError("Frankfurter returned an unexpected response")

    rows = []
    for record in payload:
        if not isinstance(record, dict):
            continue
        code = str(record.get("quote", ""))
        rate = float(record.get("rate", 0))
        if code in codes and rate > 0:
            rows.append(
                {
                    "rate_date": pd.Timestamp(record["date"]).normalize(),
                    "iso_code": code,
                    # Frankfurter returns quote-currency units per USD.
                    "usd_rate": 1.0 / rate,
                    "source": "Frankfurter v2",
                }
            )
    return pd.DataFrame(rows)


def _yahoo_ticker(code: str) -> str:
    return "BTC-USD" if code == "BTC" else f"{code}USD=X"


def _yahoo_observations(
    code: str,
    start_date: date,
    end_date: date,
    getter: JsonGetter,
) -> pd.DataFrame:
    start_timestamp = int(pd.Timestamp(start_date, tz="UTC").timestamp())
    exclusive_end = int(
        pd.Timestamp(end_date + timedelta(days=1), tz="UTC").timestamp()
    )
    payload = getter(
        YAHOO_CHART_URL.format(ticker=_yahoo_ticker(code)),
        {
            "period1": str(start_timestamp),
            "period2": str(exclusive_end),
            "interval": "1d",
            "events": "history",
        },
    )
    try:
        chart = payload["chart"]["result"][0]  # type: ignore[index]
        timestamps = chart["timestamp"]
        closes = chart["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError(f"Yahoo returned an unexpected response for {code}") from error

    rows = []
    for timestamp, close in zip(timestamps, closes, strict=True):
        if close is not None and float(close) > 0:
            rows.append(
                {
                    "rate_date": pd.to_datetime(timestamp, unit="s", utc=True)
                    .tz_localize(None)
                    .normalize(),
                    "iso_code": code,
                    "usd_rate": float(close),
                    "source": "Yahoo Finance daily close",
                }
            )
    return pd.DataFrame(rows)


def build_daily_rates(
    currencies: Iterable[str],
    start_date: date,
    end_date: date,
    getter: JsonGetter = get_json,
) -> pd.DataFrame:
    """Fetch and expand observations into one USD rate per currency and calendar day."""

    currency_names = sorted(set(currencies))
    unknown = set(currency_names) - set(CURRENCY_CODES)
    if unknown:
        raise ValueError(f"Unsupported IBM currency names: {sorted(unknown)}")
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    codes = {CURRENCY_CODES[name] for name in currency_names}
    fetch_start = start_date - timedelta(days=7)
    observations = [_frankfurter_observations(codes, fetch_start, end_date, getter)]
    for code in sorted(codes & YAHOO_ONLY_CODES):
        observations.append(_yahoo_observations(code, fetch_start, end_date, getter))

    nonempty_observations = [frame for frame in observations if not frame.empty]
    available = (
        pd.concat(nonempty_observations, ignore_index=True)
        if nonempty_observations
        else pd.DataFrame(
            columns=["rate_date", "iso_code", "usd_rate", "source"]
        )
    )
    calendar = pd.DataFrame({"txn_date": pd.date_range(start_date, end_date)})
    output = []

    for currency in currency_names:
        code = CURRENCY_CODES[currency]
        if code == "USD":
            daily = calendar.assign(
                currency=currency,
                iso_code=code,
                usd_rate=1.0,
                rate_date=calendar["txn_date"],
                source="identity",
            )
        else:
            code_rates = available.loc[available["iso_code"].eq(code)].sort_values(
                "rate_date"
            )
            if code_rates.empty:
                raise ValueError(f"No historical rates returned for {currency} ({code})")
            daily = pd.merge_asof(
                calendar.sort_values("txn_date"),
                code_rates,
                left_on="txn_date",
                right_on="rate_date",
                direction="backward",
            )
            if daily["usd_rate"].isna().any():
                raise ValueError(f"No prior rate available for {currency} ({code})")
            daily.insert(1, "currency", currency)

        output.append(daily)

    result = pd.concat(output, ignore_index=True)
    result["txn_date"] = result["txn_date"].dt.date
    result["rate_date"] = result["rate_date"].dt.date
    return result[
        ["txn_date", "currency", "iso_code", "usd_rate", "rate_date", "source"]
    ].sort_values(["txn_date", "currency"], ignore_index=True)


def write_rates_csv(frame: pd.DataFrame, output_path: str | Path) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
