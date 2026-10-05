# Local XGBoost comparison — 2026-10-05

The bounded local experiment is complete. The selected model finds **44 positive
labels in the main test top 100**, versus zero for the existing rule baseline.
No AWS training job, upload, deployment, model promotion or dashboard replacement
was performed. The treasury view is unchanged.

## Data and experiment policy

Input: `output/cloud-insights-20261005/bq1_scored.parquet`. Its SHA256 must match
the full-population dashboard manifest before the workflow accepts it. It is
derived from the successful reconciled Glue run, not the incomplete timeout output.
All 5,078,345 rows and 5,177 positive labels are accounted for across splits.

Training: September 1–6, 3,248,921 rows with 2,530 positives. Validation:
September 7–8. Test: September 9–10. September 11–18 is reported separately.
Training-only class weight: 1,283.158498 negatives per positive. No resampling.

Features: 50 float32 inputs comprising log1p prior-day USD amount, five strictly
trailing behaviour counts, self-transfer and currency-mismatch flags, cyclic hour,
and one-hot payment format/payment currency/receiving currency. Vocabularies use
training observations only, with explicit unseen-category indicators. Bank IDs
are normalized only for self-transfer comparison; account strings are preserved.
IDs, labels, absolute dates, baseline score/reasons, same-day FX and pattern
enrichment do not enter the model matrix.

Two preregistered CPU histogram candidates use depths 3 and 5, learning rate 0.1,
minimum child weight 10, seed 459, four threads, and at most 120 rounds. Early
stopping uses validation XGBoost `aucpr`, with patience 20. This internal metric
is not identical to our exact tied-score average precision. Explicitly sliced
models retain only their best validation iteration.

Candidate selection uses exact validation average precision, then precision@100,
then name. `selection.json` is written before test labels are loaded. A validation
gate requires AP improvement and non-worse precision@100 versus the rule baseline;
passing it does not trigger promotion. The selected model is not retrained on
validation, and test results were not used for weights, tree count or features.

| Candidate | Retained trees | Validation precision@100 | Validation average precision |
|---|---:|---:|---:|
| Depth 3 | 108 | 38% | 0.114007 |
| Depth 5 — selected | 120 | 46% | 0.149571 |

Depth 5's best iteration is at the configured cap. This is a bounded initial
experiment, not an exhaustive search; seeing test results is not permission to
increase that cap and select again on this same holdout.

## Same-population comparison

| Period | Rows / positives | Baseline precision@100 | Model precision@100 | Baseline AP | Model AP |
|---|---:|---:|---:|---:|---:|
| Validation | 965,524 / 1,036 | 2% | 46% | 0.007240 | 0.149571 |
| Test | 862,792 / 956 | 0% | 44% | 0.006758 | 0.135546 |
| Sparse tail | 1,108 / 655 | 60% | 98% | 0.591930 | 0.976613 |

Main test recall@100 is 4.6025%, lift@100 is 397.10. Despite the improvement,
56 of the selected 100 are label-negative and 912 of the 956 positives fall
outside that queue. This fixed-capacity queue does not detect all laundering.
The tail's very high prevalence makes it unsuitable as a primary holdout.
AP means stepwise tied-score PR area, not accuracy or trapezoidal PR-AUC.

## Artifacts and checks

`output/model-comparison-20261005/` contains two native `.ubj` models,
`features.json`, frozen `selection.json`, `comparison.json`, and label-free
top-100 queue CSVs for validation/test/tail. The selected model hash is
`f0dc65ca43901f9f02c57c09605121d48570c64d49991eb952f2d2df6a7e4765`.
XGBoost version: 3.4.1. The report records model, source, selection and feature
schema hashes. Keep the model and feature schema together for inference.

Queue explanations show the three largest signed TreeSHAP feature contributions
to the logistic margin, plus bias and total margin. Category suffixes are indexes
into the saved vocabulary (e.g. `payment_format_0` means ACH); they are not ordinal
features. The full contribution sum is checked against each queue prediction.
Explanations describe model behaviour, not causal proof of laundering.

24 tests pass in the local ML environment, with one native Spark module skipped.
Tests cover feature exclusions, train-only vocabulary/unseen categories, numeric
validation, deterministic ties and selection, frozen choice before test loading,
training-only class weights, a complete small fit/save/reload/evaluation path,
explanation reconciliation, hash rejection and output-directory protection.
The earlier Glue-container tests are separate, not an ML-container certification.

## Limitations and next step

Weighted logistic scores are **not calibrated laundering probabilities**:
[XGBoost's official imbalance guidance](https://xgboost.readthedocs.io/en/stable/tutorials/param_tuning.html)
distinguishes ranking weights from probability estimation. The pipeline plan's
previous calibration claim has been corrected. A score near 1 is not a near-100%
chance of laundering.

This is a synthetic dataset with prior whole-data EDA, incomplete early history,
and unverified timestamp timezone. Test results are exploratory, not an untouched
confirmatory production validation. The test holdout is now used; further tuning
requires a newly designed evaluation protocol, not repeated test-based selection.

Next: integrate the frozen model as an explicitly labelled dashboard comparison
and add reproducible saved-model batch inference. Preserve rule scores and BQ2
valuation. Cloud training/scoring and hosting require their own reviewed cost
and deployment steps.
