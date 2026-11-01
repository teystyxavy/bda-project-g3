# FX sensitivity of BQ2 corridor rankings

**Question.** BQ2 values every payment at the historical same-day rate. The IBM
dataset converts currencies at its own fixed rates, roughly 2020 levels. Would
the corridor answer change if we used the dataset's rates instead?

**Answer.** The leading corridors are robust; their exact order is not. The
same ten bank pairs lead under both valuations, but the top-five order changes,
and the market looks somewhat less concentrated at the dataset's rates.

Why not current (2026) rates: the transactions are dated September 2022. BQ1
uses the previous day's rate to avoid look-ahead, and BQ2 values each day's
volume at that day's rate. Today's rates would measure what the exposure is
worth now, which is a different question.

## Scope

- Run `medium-full-20261030-v2` (HI-Medium, 31,898,238 transactions,
  1 to 28 September 2022), the run behind the live dashboard.
- Inputs: that run's `bq2_daily_corridors.parquet` and `raw/fx.csv`. Their
  SHA-256 hashes match the published snapshot.
- Method: conversion is linear per day and currency, so each corridor-day's
  native amount is recovered exactly (`volume_usd / usd_rate`) and re-converted
  at the dataset's rate. Totals, HHI and top-5 share under historical rates
  reproduce the dashboard exactly.
- Dataset rates: derived from HI-Small's 72,170 cross-currency rows. Each
  currency's rate is fixed up to rounding (Euro 0.8534 and Yen 105.4 per USD).
  The run's own HI-Medium file (485,144 cross-currency rows) gives the same
  rates; the largest difference is 0.0002% (Bitcoin).

## Results

| Measure | Historical rates | Dataset rates |
|---|---|---|
| Total volume | USD 10.83 trillion | USD 11.00 trillion (+1.5%) |
| Bank-pair HHI | 0.00145 | 0.00117 |
| Top-five share | 6.5% | 5.5% |
| Top 5 / 10 / 15 / 30 overlap | — | 4 / 10 / 14 / 27 pairs |

Top five bank pairs:

| Rank | Historical rates | Dataset rates |
|---|---|---|
| 1 | 1442 → 80208 (Ruble) | 1442 → 1442 (Euro) |
| 2 | 213589 → 1209574 (Bitcoin) | 1442 → 80208 |
| 3 | 1442 → 1442 | 53839 → 5965 |
| 4 | 223769 → 171221 (Ruble) | 213589 → 1209574 |
| 5 | 53839 → 5965 | 89616 → 118554 |

Daily view: on the 27 days with at least 15 bank pairs, an average of 13.6 of
the daily top 15 match (minimum 10). The largest pair is the same on 21 of 28
days.

## Why the order moves

Each leading pair trades in a single currency, and those currencies have the
largest rate gaps. Relative to historical rates, the dataset values Bitcoin 40%
lower, Ruble 22% lower, Yen 34% higher and Euro 17% higher. US Dollar (39% of
volume) is unaffected.

The leading corridors also come from a handful of very large synthetic
payments: 1442 → 80208 is three Ruble payments, about USD 214 billion. On
8 September, five Bitcoin payments on 213589 → 1209574 make up 29% of the day's
volume. That day's HHI falls from 0.084 to 0.039 at dataset rates.

## BQ1

Barely affected: none of the cross-currency rows is laundering (0 of 72,170 in
HI-Small, 0 of 485,144 in HI-Medium), so currency choice does not change which
rows are positive.

## Reproduce

```bash
aws s3 cp s3://is459-g3-588738581042-ap-southeast-1/pipeline/runs/medium-full-20261030-v2/baseline/baseline/bq2_daily_corridors.parquet output/fx/ --profile is459-g3-tools
aws s3 cp s3://is459-g3-588738581042-ap-southeast-1/pipeline/runs/medium-full-20261030-v2/raw/fx.csv output/fx/ --profile is459-g3-tools
python scripts/local/fx-sensitivity.py --corridors output/fx/bq2_daily_corridors.parquet \
    --fx output/fx/fx.csv --transactions dataset/HI-Small_Trans.csv --output output/fx-sensitivity-1
```

The run's summary is saved as
[fx-sensitivity-20261101.json](../evidence/fx-sensitivity-20261101.json).
