# Frozen-model dashboard integration — 2026-10-05

The static dashboard now shows both the frozen XGBoost model and the original rule
baseline for the same full-population evaluation periods. This is a local snapshot,
not cloud deployment, production promotion, retraining or streaming.

Build: `scripts/build-model-dashboard.py`; output:
`output/model-dashboard-20261005/`; preview: `http://127.0.0.1:4175/`.
The original `output/cloud-insights-20261005/dashboard/` remains untouched.

## Reproducible inference

`SavedRanker` verifies the recorded hashes of the selection, feature schema and
selected native model before loading. The dashboard build also checks that the
baseline snapshot, model experiment and scored input refer to the same source
hash/population. It re-scores validation, test and sparse tail with the frozen
model, with no fitting or category refitting. Every metric matches both the saved
comparison report and the applicable original rule snapshot. The model can score
feature rows without `is_laundering`; labels are needed only to recompute evaluation.

The new snapshot records source, baseline snapshot, model, comparison, selection
and feature-schema hashes. Model queues preserve synthetic IDs as strings, omit
labels, and decode categorical feature indexes into their saved category names.
Numeric evidence uses reader-facing names; contributions themselves are unchanged.
Each queue's full contribution sum reconciles to the model score. The top-three
evidence text is a subset, not the complete sum or causal proof of laundering.

## Presentation and scope

The default AML view is explicitly the experimental frozen-model test queue.
The queue selector switches model/rules; the period selector switches test,
validation and sparse tail. A side-by-side table remains visible for both scorers.
Test results: 44/100 positives for the model versus 0/100 for rules; validation:
46/100 versus 2/100. Sparse-tail results are accompanied by their prevalence caveat.
Scores use different scales and are not calibrated probabilities.

Model review capacity is top 100, not an invented flag threshold. Alert trends and
score-distribution charts remain explicitly rule-only and follow the selected
period. Validation-only rule diagnostics retain their fixed Sep 7–8 scope.
The treasury JSON and daily trends are unchanged, including all-pair shares/HHI.

The dashboard-building skill guided same-population comparisons, preservation
of the existing views, readable evidence and explicit source/score definitions.
The project's dependency-free HTML/CSS/JavaScript implementation was retained;
no framework migration or new external data source was needed.

## Checks performed

- 24 native ML-environment tests passed; one native Spark module skipped.
- Integration tests verify preserved BQ2/rule data, reproduced model metrics,
  label-free queues, leading-zero identifiers, inference without labels, artifact
  tamper rejection and fresh-directory protection.
- Full saved-model reload reproduced all three period metrics, without retraining.
- Browser checks confirmed test/validation selections, rule/model switching,
  evidence drilldown, the sparse-tail note, model methods and provenance.
- Normal and 390-pixel viewport screenshots inspected. On the narrow view, page
  width matches content width; wide tables scroll inside their own containers.
  Temporary viewport override was reset.
- Treasury all-period/day/reset checks retain 5,078,345 rows and approximately
  USD 1.75 trillion overall; Sep 9 shows 654,467 rows and approximately USD 195.52
  billion. No browser console errors were captured in the completed checks.
- Generated label-free model CSVs match the selected-period queue data. Browser
  download-event verification timed out; end-to-end browser file download is not
  certified by this run. The CSV files are available directly in the build folder.

No new AWS calls or paid jobs occurred. Future cloud scoring/training and S3 /
CloudFront hosting need a separate reviewed cost/deployment step. Keep the existing
USD 10 budget target (updated by the user on 2026-10-05) and do not tune further
using this already-used test holdout.
