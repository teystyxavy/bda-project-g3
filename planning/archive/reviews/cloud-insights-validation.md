# Full-population insight export — 2026-10-05

Both business-question views now consume all 5,078,345 transactions from the
successful, reconciled AWS Glue output. Rule scoring and static snapshot export
run locally, not as an AWS scoring job. No new paid job, deployment, scheduling,
or public hosting was performed for this export.

## Source and reproducibility

Source: `output/full-cloud-optimized-20261005/curated/`, verified by the adjacent
`parity.json`. Cloud run and Athena reconciliation evidence are recorded in
[deployment status](../../aws-deployment-status.md). Do not use the earlier timed-out
`curated/full-20261005/transactions/` prefix.

```powershell
python scripts/dashboard/export-verified-batch.py --curated output/full-cloud-optimized-20261005/curated --verification output/full-cloud-optimized-20261005/parity.json --output output/cloud-insights-new
```

Actual output: `output/cloud-insights-20261005/`. Use a fresh directory for reruns.
The exporter checks complete unique source IDs, 5,177 positive labels and BQ2
count/USD totals against verification. It records verification and generated
Parquet hashes plus training P99 thresholds in dashboard provenance. It trusts
the previously verified feature values; it does not independently rerun feature
parity or cryptographically authenticate the input directory.

## AML results

Rules are unchanged: ACH 50 points, above training payment-format P99 20 points,
high velocity or fan-out 30 points; flagged at 60. Thresholds use September 1–6
only. Rolling features exclude simultaneous peers, and AML valuation uses prior-day
FX. Labels are evaluation-only and omitted from queue CSVs/dashboard queue rows.
Ties sort by timestamp then physical source row index. Top-100 storage is bounded
but globally ranked across batches; the all-row scored Parquet is not globally sorted.

| Period | Rows | Positives | Flagged | Precision@100 | Recall@100 | Average precision |
|---|---:|---:|---:|---:|---:|---:|
| Validation: Sep 7–8 | 965,524 | 1,036 | 891 | 2.00% | 0.1931% | 0.007240 |
| Test: Sep 9–10 | 862,792 | 956 | 886 | 0.00% | 0.00% | 0.006758 |
| Sparse tail: Sep 11–18 | 1,108 | 655 | 24 | 60.00% | 9.1603% | 0.591930 |

Average precision is the tied-score stepwise PR area, not trapezoidal PR-AUC.
Validation lift@100 is 18.64; test lift is zero. Tail prevalence is unusually high,
so its metrics do not represent normal-volume operations. Earlier cohort results
describe a different population and are not directly comparable.

Validation-only comparisons: current baseline AP 0.007240, ACH-only 0.006691,
ACH + amount 0.006809, ACH + behaviour 0.007159. No variant was automatically
promoted, and no weights or thresholds were retuned using test labels.

**The baseline is weak:** zero true positives in the main test top 100. Its coarse
scores and deterministic tie policy must remain visible limitations. The earlier
full-dataset EDA also makes the rule design exploratory. Next, compare a locally
trained model using training/validation only, document ranking metrics against
this baseline, and leave test data out of tuning. Synthetic-data results are not
universal AML claims or a production compliance decision system.

## Treasury and UI verification

Full-population historical same-day USD total reconciles to approximately
USD 1,747,869,572,934.39. There are 283,353 directed bank pairs; all-period
top-five share is 6.18% and HHI rounds to 0.0017. High volume and concentration
are treasury observations, not indicators of suspicious activity.

Browser checks at `http://127.0.0.1:4174/` confirmed full-population scope,
test/validation selection, top-100 evidence drilldown, both dashboard tabs,
all-period totals and daily selection. September 9 displays 654,467 transactions
and approximately USD 195.52 billion. No browser console errors were captured.

Native tests: 21 passed, one Spark module skipped because native PySpark is absent.
New tests compare batched metrics/queues/ties with the existing exact evaluator
and export a small complete fixture with score, threshold, scope and label checks.
The prior 30-test Glue-container result remains separate; the new exporter tests
were run natively, not re-certified in that container.
