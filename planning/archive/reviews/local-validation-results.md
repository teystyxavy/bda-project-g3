# Local validation — 2026-10-04

The complete HI-Small transformation and BQ2 aggregation processed 5,078,345
transactions, including 5,177 laundering labels. No sender or receiver entity
joins were missing, no USD values were missing, and corridor counts reconciled
to the input count. This validates local processing, not an AWS deployment.

`output/analysis-20261004/` contains the full-data daily corridors, rankings,
and bank-pair concentration series. The BQ1 hash-selected sender cohort contains
256,557 rows across all 15 currencies and 14 dates, with complete outgoing
history for each selected sender. This preserves velocity and fan-out features
within the available dataset history. It does not establish inbound behaviour.

## Revised chronological evaluation

The original planned dates produced nine validation rows and no test rows in
the cohort. A full-file daily count showed that September 11–18 contains only
1,108 rows, including 655 positives. The bulk-data evaluation now trains P99
thresholds on September 1–6, validates September 7–8, tests September 9–10, and
reports the sparse late tail separately. The revision follows volume coverage,
not optimization of model performance.

`output/evaluation-20261004/evaluation.json` supersedes the initial AML metrics
in `output/analysis-20261004/run_report.json`. The latter remains the record of
the initial run and full transformation checks; its original split is not used
for reported performance. Current runner defaults use the revised split.

| Cohort split | Transactions | Positives | Precision@100 | Recall@100 | Lift@100 | Average precision |
|---|---:|---:|---:|---:|---:|---:|
| Validation | 48,882 | 66 | 2% | 3.03% | 14.81 | 0.00982 |
| Test | 43,734 | 43 | 2% | 4.65% | 20.34 | 0.00762 |

The test queue finds two positives in its first 100 transactions. The high lift
reflects the low base rate; 98 of those 100 entries are false positives. These
are exploratory baseline results on a sender cohort, not proof of production
performance or full-data model quality. Earlier EDA inspected the whole dataset,
rule weights are hypotheses, and many scores tie. Ties use timestamp followed
by source row; average precision evaluates ties as score groups.

BQ1 uses prior-calendar-day FX; BQ2 uses same-day historical valuation. The
dataset timezone and source publication times are not verified. Initial rolling
windows have no history from before September 1. The seven-day window is still
warming up during early evaluation dates.

## Verification and next milestone

17 automated tests pass, including window boundary, simultaneous-event,
cross-bank account, score/label separation, tied-score metrics, corridor
reconciliation, and complete fixture-run checks. The full-data run additionally
verifies the real account/FX joins and reconciled BQ2 output.

The local static dashboard now consumes revised scores and full-data corridors.
Browser checks confirmed both views, validation/tail period changes, a daily
treasury filter, and transaction evidence drilldown. Dashboard tests cover
full-population concentration denominators, empty queues, label omission,
and independence of validation diagnostics from test-label changes.

## Validation-only rule diagnosis

| Variant | Precision@100 | Recall@100 | Average precision |
|---|---:|---:|---:|
| Current baseline | 2% | 3.03% | 0.009823 |
| ACH only | 1% | 1.52% | 0.008388 |
| ACH + amount | 2% | 3.03% | 0.009333 |
| ACH + behaviour | 1% | 1.52% | 0.008946 |

In the baseline validation top 100, 51 rows receive ACH-only points and all are
false positives. The other 49 receive ACH plus above-training-P99 points; two
are positive and 47 are false positives. Thus 98 of 100 are false positives.
No simpler variant improves precision@100; keep the current baseline as the
comparison benchmark rather than claiming a successful operational detector.
These diagnostics do not change the held-out test scores or tune against them.

Next: prepare Terraform for the core batch pipeline and its deployment runbook;
actual cloud creation requires explicit approval. Full-population BQ1 scoring,
ML, AWS deployment, orchestration, and streaming remain pending. Preserve the
test period for reporting and avoid repeated tuning on its results.
