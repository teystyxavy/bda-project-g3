# Local Glue optimization — 2026-10-05

The first full cloud run timed out at 600 seconds. Its partial output remains
invalid; these local results do not retroactively validate it.

## Changes

- Replace repeated Spark range-window distinct-set construction with one
  partitioned sort and a streaming queue/counter scan. Every row enters and
  leaves each history queue once. State resets per normalized sender.
- Freeze the feature snapshot for a timestamp, excluding all simultaneous
  peers. Keep inclusive lower bounds, exact distinct counterparties, raw
  identifiers, and label-independent features.
- Combine transaction quality checks into one scan and post-join coverage/FX
  checks into one scan. Invalid metadata keys and duplicate FX keys still fail.
- Release cached raw rows after curated rows are materialized; log timings for
  input, curation, and features/write/read-back phases separately.

## Verified evidence

Official Glue 5 container (same pinned image as the existing test helper),
Spark local[4], 4 GB driver memory, eight shuffle partitions:

| Check | Result |
|---|---|
| Dense 24,000-row single-sender original windows | 14.342 seconds |
| Same dense fixture with queues | 1.417 seconds; identical five feature totals |
| Dense speedup | 10.12× (one run, not a cloud forecast) |
| Full input load and sender profile | 22.818 seconds; 5,078,345 rows |
| Sender profile | 496,999 normalized senders; largest has 168,672 rows |
| Full history feature calculation | 38.561 seconds; all 5,078,345 rows consumed |
| Exact reference comparison | Five features × 256,557 cohort rows; zero mismatches |
| Glue-container tests | 30 passed; 35 upstream deprecation warnings |
| Native tests | 19 passed; Spark module skipped |

The input profile confirms significant sender skew. The dense fixture supports
the overlapping-window performance diagnosis, but does not by itself prove
the reason for every slow task in the AWS run. The new scan is linear after
sorting, with memory proportional to one sender's active seven-day history.

Evidence: `output/history-benchmark-20261005/report.json`. Reproduce using
`scripts/benchmark-glue-history.py` under the official Glue container via
`spark-submit --master local[4] --driver-memory 4g`, supplying `--transactions`
for the unchanged source CSV, `--reference` for the local scored cohort Parquet,
and `--report` for a fresh JSON file. The script makes no AWS calls. Reference
comparison reads only integer ID/feature columns, avoiding the old pandas
nanosecond timestamp encoding unsupported by Spark 3.5 schema inference.

## Complete local pipeline — passed

After the user requested another approval attempt, the full local Docker run
was approved and completed successfully. Same pinned Glue image, local[4],
4 GB driver, eight shuffle partitions; no AWS credentials were mounted.

| Phase | Seconds |
|---|---|
| Input load/count | 28.239 |
| Curation, metadata/FX checks and materialization | 43.293 |
| History features, Parquet write and row-count read-back | 91.461 |
| Total measured job processing | 162.992 |

`CURATION_VERIFIED rows=5078345` was emitted. The separate
`scripts/verify-aws-full.py` reconciliation then passed:

- All 5,078,345 source IDs unique, correctly named and present.
- 5,177 positive labels; no missing account metadata or USD values.
- Five history features and key transaction fields match the 256,557-row
  saved cohort reference.
- All 1,099,714 daily bank/currency corridor aggregates match the full local
  reference (floating comparisons use rtol 1e-10 and atol 1e-6).
- Total USD-equivalent volume: 1,747,869,572,934.3916.

Evidence: `output/full-local-optimized-20261005/run.log`, `curated/`, and
`parity.json`. Runtime excludes container startup and the separate pandas
reconciliation. These local timings do not guarantee AWS runtime.

Next: propose a bounded cloud retry with fresh output prefix and reviewed
catalog-location/script updates. No AWS calls, deployments or paid runs were
made during this optimization or its local verification. The optimized script
is not deployed; the prior incomplete cloud output remains invalid.

## Subsequent cloud verification

The user later approved one retry. The optimized script was deployed and the
cloud job succeeded in 386 reported execution seconds (341.204 application
processing seconds). Athena reconciliation and downloaded full-output parity
both passed. See [deployment status](../../aws-deployment-status.md) for run/query
identifiers, costs and evidence. The old partial cloud output remains invalid;
only `curated/full-optimized-20261005/transactions/` is verified.
