# Proposal: improve the BQ1 XGBoost ranker's performance

Status: research complete. **Improvement #1 implemented** (code + non-Spark
tests; see [the spec's implementation record](improvement_1_spec.md) for
exact status, including the one still-open step). #2–#5 not started. This
document records the file inventory, measured baseline and the five
highest-ROI improvements identified; see
local model validation (archived) for
the frozen comparison this proposal works from.

## Objective and measured baseline

The current frozen model (depth-5 XGBoost, selected per
`model.choose_candidate`) is a real improvement over the rule baseline, but
there is clear, code-visible headroom left on the table — this proposal is
about capturing it, not about replacing the approach.

| Period | Rows / positives | Baseline precision@100 | Model precision@100 | Baseline AP | Model AP |
|---|---:|---:|---:|---:|---:|
| Validation | 965,524 / 1,036 | 2% | 46% | 0.007240 | 0.149571 |
| Test | 862,792 / 956 | 0% | 44% | 0.006758 | 0.135546 |
| Sparse tail | 1,108 / 655 | 60% | 98% | 0.591930 | 0.976613 |

Test recall@100 is 4.60%, lift@100 is 397. Class imbalance: ~1,283 negatives
per positive in training (Sep 1–6, 3,248,921 rows, 2,530 positives). The
selected depth-5 candidate retained all 120 trees — its best iteration was at
the configured round cap, not at an early-stopping point (see §2 below).

## File inventory — everything involved in training

| File | Role |
|---|---|
| `src/bda_pipeline/model.py` | Core pipeline: `fit_encoder`/`encode` (feature engineering → numeric matrix), `compare_models` (training loop, hyperparameters, candidate selection), `CANDIDATES` (hyperparameter grid — currently tree depth 3 vs. 5 only), `SPLITS` (chronological train/validation/test/tail), `SavedRanker` (frozen-model loading for inference) |
| `src/bda_pipeline/analytics.py` | `add_behaviour` (the 5 velocity/fan-out features, sender-side only), `score_baseline` (rule baseline compared against), `ranking_metrics` (the exact evaluation metric — tied-score average precision, precision/recall/lift@100) |
| `src/bda_pipeline/batch.py` | `FEATURES` constant (the 5 feature names, imported into `model.py`), `QUEUE_COLUMNS`, `PERIODS` |
| `src/bda_pipeline/sagemaker_training.py` | `train_channels` (container entry logic), `build_request` — the bounded contract: 1× `ml.m5.xlarge`, rounds capped 1–120, runtime capped 60–1800s |
| `sagemaker/train.py` | Docker `ENTRYPOINT`; reads hyperparameters, calls `train_channels` |
| `sagemaker/requirements.txt` | Pinned deps — `xgboost-cpu==3.4.1`, numpy, pandas, pyarrow, scipy |
| `jobs/curate_transactions.py` | Spark/Glue parity version of `add_behaviour` — must match it exactly; any feature change here needs a matching Spark change |
| `src/bda_pipeline/inference.py` | Consumes the frozen model at serving time (Glue batch + streaming) — any feature/schema change touches this too |
| `scripts/modeling/compare-local-model.py` | Local CLI entry point for `compare_models` |
| `scripts/modeling/sagemaker-training.py` | Cloud CLI: prepare/start/status/collect a SageMaker training job |
| `infra/sagemaker/*.tf` | ECR repository + execution role only — the compute shape/round cap live in `sagemaker_training.py`, not Terraform |
| `tests/test_model.py`, `tests/test_sagemaker_training.py`, `tests/spark/test_glue_parity.py` | Correctness tests; any feature/hyperparameter change needs matching test updates |

## Top 5 improvements, ranked by expected ROI

### 1. Add receiver-side (`to_account`) behaviour features

See [the detailed implementation spec](improvement_1_spec.md) for the full
step-by-step plan, including the streaming-scope decision and exact files touched.

`add_behaviour` computes velocity/fan-out only for the sender (`from_account`);
the receiver side has no rolling features at all. This project's own EDA found
GATHER-SCATTER and SCATTER-GATHER are the *dominant* pattern types by
transaction volume in this dataset — both defined by a receiver accumulating
money from many senders — so the model structurally cannot see its own
dataset's dominant pattern today. Literature confirms this is a real, named
gap: the MonLAD and Rwandan-mobile-money AML papers both treat in-degree/fan-in
and counterparty-diversity on the receiving side as core features, and the
Elliptic graph-feature guide specifically calls out in-degree vs. out-degree
asymmetry as a top AML signal.

**Implementation:** mirror `add_behaviour`'s existing sliding-window logic onto
`to_account` (incoming count 1h/24h/7d, distinct senders 24h/7d — same
algorithm, grouped by receiver instead of sender). Touches `analytics.add_behaviour`,
`jobs/curate_transactions.py` (Spark parity), `batch.FEATURES`, the Glue
catalog schema, and `inference.py`.

### 2. Raise the boosting-round cap

`local-model-validation.md` states the selected depth-5 model's best iteration
was at the configured 120-round cap in `sagemaker_training.build_request`'s
bounded contract — early stopping (patience 20) never triggered. Validation
AUC-PR was still improving when training was cut off by the hard limit, not by
convergence. This is the cheapest fix on this list: raise `rounds` and the
matching `StoppingCondition.MaxRuntimeInSeconds`, and let early stopping
actually decide when to stop.

### 3. Expand the hyperparameter search beyond tree depth

Only `max_depth` (3 vs. 5) is varied; `eta=0.1`, `min_child_weight=10`,
`subsample=1`, `colsample_bytree=1`, and all L1/L2 regularization are
hardcoded. XGBoost's own tuning guide recommends `max_delta_step` (try 1–10)
specifically for this imbalance regime — it stabilizes early boosting rounds
when `scale_pos_weight` alone is being used at a large ratio (here, ~1,283).

**Implementation:** add `max_delta_step`, `subsample`, and `reg_lambda`/`reg_alpha`
as a small, **pre-registered** grid in `CANDIDATES` — decided before training,
consistent with the project's existing "frozen before test" discipline. Not
open-ended tuning.

### 4. Add rolling-sum and relationship-novelty features

Velocity is currently count-only; the only amount check is a global per-format
P99 threshold used in the rule baseline, so the model's one amount signal is
the single transaction's own `log1p_amount_usd_asof`. A sender making many
transactions just under the threshold (structuring/smurfing) is invisible to
both. Add:

- rolling **USD sum** sent in 24h/7d (not just count);
- amount relative to the sender's own trailing average (a per-account anomaly
  signal, distinct from the global threshold);
- counterparty-novelty — has this sender ever paid this receiver before /
  corridor age.

The KnowFeat and Rwandan-mobile-money papers both treat fan-in/fan-out *ratio*
and counterparty-diversity as stronger signals than raw counts alone. These
are cheap additions to the same `add_behaviour` sliding-window machinery
already in place, and pair naturally with #1 (same mechanism, same Spark-parity
work).

### 5. Fix the early-stopping/selection metric mismatch

`local-model-validation.md` flags this directly: early stopping uses XGBoost's
built-in `aucpr` (trapezoidal AUC-PR), but candidate selection uses the
project's own *exact tied-score* average precision (`ranking_metrics`/
`choose_candidate`) — the model being early-stopped optimizes a metric that
isn't quite the one it's judged on.

**Implementation:** wire the project's own `ranking_metrics` AP calculation in
as a custom XGBoost evaluation metric (`feval`/`custom_metric`) for early
stopping, so the stopping decision is consistent with the actual selection
rule. Do this once #3's `max_delta_step` candidates exist, so they can be
compared on the metric that actually matters.

## Suggested order

1. **#2 first** (raise round cap) — nearly free, no feature/schema changes,
   and directly addresses evidence the current model is undertrained. Worth
   doing in isolation to see if it alone moves the needle before anything else
   changes.
2. **#1 and #4 together** — the real feature-engineering lift. Same
   `add_behaviour` mechanism and Spark-parity work, so design and implement in
   one pass rather than two.
3. **#3 and #5 last** — best done once the final feature set from #1/#4
   exists, so the hyperparameter search and the early-stopping metric fix are
   tuning the model that will actually ship, not one about to change again.

## Limitations

This is a research/planning document, not an implementation. Any change here
still needs: matching Spark parity changes and tests (`jobs/curate_transactions.py`,
`tests/spark/test_glue_parity.py`), re-running the chronological comparison
with the project's existing "frozen before test" discipline (no repeated
test-set selection), and — per the project's own stated policy — does not
automatically promote a new model or replace the dashboard.

## Sources

- [XGBoost Notes on Parameter Tuning](https://xgboost.readthedocs.io/en/release_1.6.0/tutorials/param_tuning.html)
- [XGBoost parameter tuning (GitHub doc)](https://github.com/dmlc/xgboost/blob/72451457120ac9d59573cf7580ccd2ad178ef908/doc/how_to/param_tuning.md)
- [Detecting Money Laundering in Rwandan Mobile Money: A Machine Learning Framework](https://arxiv.org/pdf/2608.15447)
- [MonLAD: Money Laundering Agents Detection in Transaction Streams](https://arxiv.org/pdf/2201.10051)
- [Elliptic: Transaction Graph Feature Engineering for Illicit Flow Detection and Risk Scoring](https://www.elliptic.co/corpus/gen-3864/blockchain-analysis/transaction-graph-feature-engineering-for-illicit-flow-detection-and-risk-scoring.html)
- [KnowFeat: Knowledge-Guided Feature Engineering via LLM Agents](https://arxiv.org/pdf/2609.03529)
