"""Historical FX ingestion for the IBM AML transaction currencies.

Why this module exists
----------------------
BQ2 (treasury / correspondent-bank corridor volume and concentration) has to
add up payments made in 15 different currencies, so every amount must be
converted into one common unit: US dollars. The Kaggle dataset ships no
exchange rates, so this module fetches *historical daily* rates for exactly the
dates and currencies that appear in the transaction file.

Where it sits in the pipeline
-----------------------------
Kaggle + FX rates -> S3 raw zone -> Glue curation. This module produces the FX
half of that raw input. It is called by:

* ``scripts/ingestion/fetch-fx-rates.py`` (local CLI; writes
  ``dataset/fx_rates.csv`` by default), and
* ``acquisition.py`` (the verified acquisition step, which is also packaged
  into the cloud-ingestion Lambda container image, ``cloud_acquisition.py``,
  that fetches the raw files and uploads them to S3).

The resulting CSV is read by ``transform.build_curated_transactions`` locally
and by the Glue job ``jobs/curate_transactions.py`` in AWS, which joins it on
(txn_date, currency) to compute ``amount_usd`` (same-day rate) and
``amount_usd_asof`` (previous-day rate, used by the model so it never "sees"
a rate published after the transaction).

Data sources
------------
* Frankfurter v2 (European Central Bank reference rates) for most currencies.
* Yahoo Finance daily closes for BTC, RUB and SAR, which Frankfurter cannot
  provide for September 2022.

Network behaviour: only ``get_json`` touches the network. Every other function
takes a ``getter`` argument so tests can inject canned JSON instead.

Output shape (``build_daily_rates``): one row per (calendar day, currency) with
columns ``txn_date, currency, iso_code, usd_rate, rate_date, source`` where
``usd_rate`` = USD per 1 unit of the currency.
"""

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
# Values are ISO 4217 codes (BTC is the de-facto code for Bitcoin); these are
# what the FX APIs understand.
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
# (The ECB stopped publishing RUB in March 2022, never published SAR, and does
# not cover crypto.) They are fetched from Yahoo Finance instead.
YAHOO_ONLY_CODES = {"BTC", "RUB", "SAR"}

FRANKFURTER_RATES_URL = "https://api.frankfurter.dev/v2/rates"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

# Type of a function that takes (url, query-params) and returns parsed JSON.
# ``get_json`` is the real implementation; tests pass a fake with this shape.
JsonGetter = Callable[[str, dict[str, str]], object]


def get_json(url: str, params: dict[str, str], attempts: int = 3) -> object:
    """Fetch JSON with bounded retries and a descriptive user agent.

    Args:
        url: Base URL without a query string.
        params: Query parameters; URL-encoded and appended when non-empty.
        attempts: Total tries before giving up (default 3).

    Returns:
        The parsed JSON body (``dict`` or ``list``).

    Retry/failure behaviour:
        HTTP errors, network errors and timeouts are retried with exponential
        backoff (sleep 1 s, then 2 s, ...; no sleep after the last attempt).
        Each request has a 30-second timeout. After the final failure a
        ``RuntimeError`` is raised that names the URL and chains the original
        error, so the pipeline stops instead of writing incomplete rates.
    """

    request_url = f"{url}?{urlencode(params)}" if params else url
    # Public APIs (Yahoo in particular) may reject the default Python user
    # agent; a named agent also identifies the project politely.
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
            # Backoff: 2**0 = 1 s, 2**1 = 2 s, ... but only if a retry remains.
            if attempt < attempts - 1:
                time.sleep(2**attempt)
    raise RuntimeError(f"Could not fetch {request_url}: {last_error}") from last_error


def discover_requirements(
    transactions_path: str | Path,
    nrows: int | None = None,
    chunksize: int = 250_000,
) -> tuple[date, date, list[str]]:
    """Find the transaction date range and payment currencies without loading all rows.

    The full transaction CSV is millions of rows, so it is streamed in chunks
    and only the two columns needed here are parsed.

    Args:
        transactions_path: Raw Kaggle transaction CSV (original headers, e.g.
            "Timestamp", "Payment Currency").
        nrows: Optional cap on rows inspected (matches the smoke-run sample).
        chunksize: Rows per chunk; bounds memory use.

    Returns:
        ``(earliest_date, latest_date, sorted_currency_names)`` - the inclusive
        calendar range and the set of payment currencies FX is needed for.

    Raises:
        ValueError: if the file is empty or contains a currency name that is
            not in ``CURRENCY_CODES`` (we would not know how to price it).
    """

    earliest: date | None = None
    latest: date | None = None
    currencies: set[str] = set()
    # Counts down how many more rows we are allowed to inspect (None = all).
    rows_remaining = nrows

    # chunksize turns read_csv into an iterator of DataFrames.
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
            # Trim the last chunk so exactly ``nrows`` rows are considered.
            chunk = chunk.iloc[:rows_remaining]
            rows_remaining -= len(chunk)

        timestamps = pd.to_datetime(chunk["Timestamp"], errors="raise")
        chunk_earliest = timestamps.min().date()
        chunk_latest = timestamps.max().date()
        # Running min/max across chunks (first chunk initialises them).
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
    """Fetch ECB-based daily rates from Frankfurter for the supported codes.

    Only one HTTP request is made for all currencies and the whole date range.
    Frankfurter only publishes on ECB business days, so weekends/holidays are
    absent here; ``build_daily_rates`` fills those gaps afterwards.

    Args:
        codes: ISO codes wanted. USD (identity) and ``YAHOO_ONLY_CODES`` are
            removed because they are handled elsewhere.
        start_date, end_date: Inclusive range to request.
        getter: JSON fetch function (``get_json`` in production).

    Returns:
        DataFrame with ``rate_date, iso_code, usd_rate, source``. ``usd_rate``
        is USD per 1 unit of the currency. An empty (but correctly-columned)
        frame is returned when nothing needs fetching.

    Raises:
        ValueError: if the API response is not a JSON list.
    """
    codes = sorted(set(codes) - {"USD"} - YAHOO_ONLY_CODES)
    if not codes:
        return pd.DataFrame(columns=["rate_date", "iso_code", "usd_rate", "source"])

    # base=USD means each returned rate is "units of quote currency per 1 USD".
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
        # Skip anything malformed rather than crashing on one bad record;
        # missing currencies are caught later by build_daily_rates.
        if not isinstance(record, dict):
            continue
        code = str(record.get("quote", ""))
        rate = float(record.get("rate", 0))
        # rate > 0 also protects the 1/rate division below.
        if code in codes and rate > 0:
            rows.append(
                {
                    "rate_date": pd.Timestamp(record["date"]).normalize(),
                    "iso_code": code,
                    # Frankfurter returns quote-currency units per USD.
                    # Inverting gives USD per 1 unit, e.g. 0.95 EUR/USD -> 1.0526 USD/EUR.
                    "usd_rate": 1.0 / rate,
                    "source": "Frankfurter v2",
                }
            )
    return pd.DataFrame(rows)


def _yahoo_ticker(code: str) -> str:
    """Map an ISO code to Yahoo's symbol, e.g. RUB -> ``RUBUSD=X``, BTC -> ``BTC-USD``.

    Both forms are quoted as USD per 1 unit of the currency, so no inversion is
    needed for Yahoo data.
    """
    return "BTC-USD" if code == "BTC" else f"{code}USD=X"


def _yahoo_observations(
    code: str,
    start_date: date,
    end_date: date,
    getter: JsonGetter,
) -> pd.DataFrame:
    """Fetch daily closing prices for one currency from Yahoo Finance's chart API.

    Args:
        code: ISO code (one of ``YAHOO_ONLY_CODES``).
        start_date, end_date: Inclusive calendar range.
        getter: JSON fetch function.

    Returns:
        DataFrame with ``rate_date, iso_code, usd_rate, source`` where
        ``usd_rate`` is the daily close (USD per unit). Days with a missing or
        non-positive close are skipped.

    Raises:
        ValueError: if the response does not have the expected chart layout.
    """
    # Yahoo takes Unix timestamps (seconds, UTC). period2 is exclusive, so we
    # pass midnight *after* end_date to include the whole last day.
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
    # Expected shape: {"chart": {"result": [{"timestamp": [...],
    #   "indicators": {"quote": [{"close": [...]}]}}]}}
    try:
        chart = payload["chart"]["result"][0]  # type: ignore[index]
        timestamps = chart["timestamp"]
        closes = chart["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError(f"Yahoo returned an unexpected response for {code}") from error

    rows = []
    # strict=True: the two lists must be the same length or zip raises.
    for timestamp, close in zip(timestamps, closes, strict=True):
        if close is not None and float(close) > 0:
            rows.append(
                {
                    # Convert epoch seconds -> UTC datetime -> naive date at
                    # midnight, so it compares cleanly with the calendar.
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
    """Fetch and expand observations into one USD rate per currency and calendar day.

    Transactions happen every day, but markets publish rates only on business
    days. For each calendar day we therefore use the most recent published rate
    on or before that day ("last observation carried forward"), which never
    uses a rate from the future.

    Args:
        currencies: IBM currency names (e.g. from ``discover_requirements``).
        start_date, end_date: Inclusive range of transaction dates to cover.
        getter: JSON fetch function; defaults to the real ``get_json``.

    Returns:
        DataFrame sorted by (txn_date, currency) with columns
        ``txn_date`` (the transaction day), ``currency`` (IBM name),
        ``iso_code``, ``usd_rate`` (USD per unit), ``rate_date`` (the day the
        rate was actually published) and ``source`` (provenance string).
        Exactly one row per currency per day, as the curation join requires.

    Raises:
        ValueError: for unknown currency names, an inverted date range, a
            currency with no data at all, or a day with no earlier rate.
    """

    currency_names = sorted(set(currencies))
    unknown = set(currency_names) - set(CURRENCY_CODES)
    if unknown:
        raise ValueError(f"Unsupported IBM currency names: {sorted(unknown)}")
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date")

    codes = {CURRENCY_CODES[name] for name in currency_names}
    # Fetch an extra week before the start so that if start_date falls on a
    # weekend/holiday there is still an earlier business-day rate to carry
    # forward.
    fetch_start = start_date - timedelta(days=7)
    observations = [_frankfurter_observations(codes, fetch_start, end_date, getter)]
    # One Yahoo request per Yahoo-only currency actually present in the data.
    for code in sorted(codes & YAHOO_ONLY_CODES):
        observations.append(_yahoo_observations(code, fetch_start, end_date, getter))

    # pd.concat of only-empty frames would lose dtypes/columns, so build an
    # explicit empty frame in that case.
    nonempty_observations = [frame for frame in observations if not frame.empty]
    available = (
        pd.concat(nonempty_observations, ignore_index=True)
        if nonempty_observations
        else pd.DataFrame(
            columns=["rate_date", "iso_code", "usd_rate", "source"]
        )
    )
    # Every calendar day in the requested range (weekends included).
    calendar = pd.DataFrame({"txn_date": pd.date_range(start_date, end_date)})
    output = []

    for currency in currency_names:
        code = CURRENCY_CODES[currency]
        if code == "USD":
            # USD -> USD is always 1.0; no API call needed.
            daily = calendar.assign(
                currency=currency,
                iso_code=code,
                usd_rate=1.0,
                rate_date=calendar["txn_date"],
                source="identity",
            )
        else:
            # merge_asof requires both sides sorted on the join key.
            code_rates = available.loc[available["iso_code"].eq(code)].sort_values(
                "rate_date"
            )
            if code_rates.empty:
                raise ValueError(f"No historical rates returned for {currency} ({code})")
            # direction="backward": for each txn_date pick the latest rate_date
            # that is <= txn_date (carry the last known rate forward).
            daily = pd.merge_asof(
                calendar.sort_values("txn_date"),
                code_rates,
                left_on="txn_date",
                right_on="rate_date",
                direction="backward",
            )
            if daily["usd_rate"].isna().any():
                raise ValueError(f"No prior rate available for {currency} ({code})")
            # Put the IBM currency name right after txn_date.
            daily.insert(1, "currency", currency)

        output.append(daily)

    result = pd.concat(output, ignore_index=True)
    # Store plain dates (not timestamps) so the CSV holds YYYY-MM-DD values.
    result["txn_date"] = result["txn_date"].dt.date
    result["rate_date"] = result["rate_date"].dt.date
    return result[
        ["txn_date", "currency", "iso_code", "usd_rate", "rate_date", "source"]
    ].sort_values(["txn_date", "currency"], ignore_index=True)


def write_rates_csv(frame: pd.DataFrame, output_path: str | Path) -> None:
    """Save the daily rate table as CSV, creating parent folders if needed.

    Args:
        frame: Output of ``build_daily_rates``.
        output_path: Destination file, e.g. ``dataset/fx_rates.csv``. This file
            is what gets uploaded to the S3 raw zone for the Glue curation job.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(output_path, index=False)
