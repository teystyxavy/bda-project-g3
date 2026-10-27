# Spec: implement improvement #1 — receiver-side behaviour features

Status: **steps 1–7 and 9 implemented** (`analytics.py`, `curate_transactions.py`,
`batch.py`, `inference.py`/`model.py` picked it up automatically, `streaming.py`
explicitly scoped out per Step 0, `catalog.tf`, tests). **Step 8 (re-run the
training comparison) not done** — this machine has no local `bq1_scored.parquet`
to train against; see §Step 8 below for what's needed to complete it. Detailed
implementation spec for item #1 of the
[model performance improvement proposal](model-performance-improvement-proposal.md)
("Add receiver-side (`to_account`) behaviour features").

## Implementation record

- `src/bda_pipeline/analytics.py`: `add_behaviour` refactored into a shared
  `_windowed_counts` helper called twice (sender/outgoing, receiver/incoming).
  Non-Spark suite: **216 passed, 4 skipped** (`tests --ignore=tests/spark`),
  including two new fan-in tests
  (`test_fan_in_counts_distinct_senders_not_the_current_rows_own_sender` and
  new assertions on the existing boundary test).
- `jobs/curate_transactions.py`: `add_behaviour` refactored the same way for
  Spark, with a shared `_windowed_counts` helper that returns
  `source_row_index` + 5 features, joined back onto `frame` twice (once per
  direction). Spark-container parity test status: see below.
- `src/bda_pipeline/batch.py`: `FEATURES` grown to 10 (5 outgoing + 5
  incoming); `COLUMNS` picked this up automatically via `*FEATURES`.
- `src/bda_pipeline/streaming.py`: left intentionally computing only the 5
  outgoing features (`SENDER_FEATURES`, explicit list, not derived from the
  now-10-item `FEATURES`) — see Step 0. Module docstring updated to state
  this explicitly rather than leave it as a latent gap.
- `src/bda_pipeline/model.py`: `SavedRanker.queue()`'s alias dict extended
  with 5 more human-readable labels for the dashboard explanation strings.
- `infra/terraform/catalog.tf`: the `full_transactions` table's behavioural
  columns list extended with the 5 new `bigint` columns. `terraform fmt`/
  `validate` pass.
- Tests updated for the 3 shared fixtures that needed the new columns added
  (`tests/test_batch.py`'s `fixture()`, `tests/test_model.py`'s `sample()`).
  `tests/spark/test_glue_parity.py` extended with an explicit fan-in spot
  check and the label-independence test's column filter.

## Step 0 — a decision to make first: streaming scope

`FEATURES` (defined in `batch.py`) is shared by **three** independent
implementations that must stay in exact parity: `analytics.add_behaviour`
(pandas, batch), `curate_transactions.add_behaviour` (Spark, Glue), and
`streaming.transition` (Lambda). The first two already process the *complete*
historical dataset, so adding a receiver-side grouping pass is straightforward
for both. The streaming path is structurally different: Kinesis partitions by
**sender** specifically so "one sender's events stay on one shard in order"
(`handle_records`'s own comment) — `transition()` only ever sees one sender's
history. A receiver's incoming transactions can arrive from senders on
*other* shards, so a single Lambda invocation cannot maintain an accurate
receiver-side rolling window without a real architecture change (e.g. a
second DynamoDB-backed receiver-keyed store, updated non-transactionally from
whichever shard happens to process each event — a genuine redesign, not a
feature tweak).

**Decision: scope this improvement to the batch path only** (training + Glue
batch scoring); explicitly exclude the streaming demo, which is already
parked/disabled, so this doesn't block anything live. Document it as a known
follow-up rather than quietly breaking `streaming.py`.

## Step 1 — design the new features

Mirror the existing 5 exactly, swapping sender → receiver:

```
in_txn_count_1h, in_txn_count_24h, in_txn_count_7d,
distinct_senders_24h, distinct_senders_7d
```

Same semantics as the sender side: strict `[t-window, t)`, same-timestamp
peers excluded, counterparty = the *other* side of the transaction (for
receiver features, that's the sender).

**Naming note, stated explicitly so it isn't left implicit:** the 5 existing
features keep their current unprefixed names and are **not** renamed — they
describe the sender's *outgoing* activity, which was simply the only
direction tracked until now. The 5 new features are prefixed `in_` for the
receiver's *incoming* activity. This asymmetry (unprefixed = out, `in_` = in)
is deliberate, not an oversight — a fully symmetric `out_`/`in_` naming was
considered and rejected for this pass because the existing unprefixed names
are referenced by literal string outside the `FEATURES` list (the rule
baseline's `score_baseline` condition in `analytics.py`, the same condition
repeated in `batch.py`'s validation ablation, and the fan-out threshold
documented in `planning/big-data-pipeline-plan.md` §4.3) and already exist as
columns in deployed, populated curated Parquet — renaming them is a separate,
larger-scope change than adding the 5 new ones. Carry this same note into the
`FEATURES` comment in `batch.py` when implementing step 4.

## Step 2 — `src/bda_pipeline/analytics.py` (pandas)

`add_behaviour` currently does one `groupby(['_bank', 'from_account'])` pass
with a per-sender deque/Counter scan. Add a second, structurally identical
pass grouped by `(_to_bank, to_account)` instead, using `from_bank`/
`from_account` as the "counterparty" tuple instead of `to_bank`/`to_account`.

Implementation approach: factor the existing inner loop (the
`while pos < len(records)` block) into a shared helper parameterized by which
side is "self" vs. "counterparty", call it twice, and merge the two feature
blocks into `result` before returning. Keep the exact same tie-breaking
(`sort_values(['timestamp', 'source_row_index'])`) and cutoff-inclusive-at-
boundary logic — any divergence here breaks Spark parity.

## Step 3 — `jobs/curate_transactions.py` (Spark)

Same idea: `add_behaviour` currently does one
`repartition("_sender_bank", "from_account").sortWithinPartitions(...)` +
`mapPartitions(scan)`. Add a second DataFrame built by repartitioning/sorting
on `("_party_bank", "to_account")` instead and running an analogous `scan`
that walks incoming transactions. Then join the two five-column outputs back
onto the original frame on `transaction_id` (or `source_row_index`, unique and
cheaper to join on than the string ID) to produce one row per transaction with
all 10 behaviour columns. Keep `add_behaviour` returning the same documented
contract (new columns appended, `LongType`, non-null) so `main()`'s
`--behaviour true` path and the row-count checks in `main()` don't need to
change.

## Step 4 — `src/bda_pipeline/batch.py`

```python
FEATURES = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
            'distinct_counterparties_24h', 'distinct_counterparties_7d',
            'in_txn_count_1h', 'in_txn_count_24h', 'in_txn_count_7d',
            'distinct_senders_24h', 'distinct_senders_7d']
```

Also add the 5 new names to `COLUMNS` (the fixed read-list from curated
Parquet) — leaving them out means `export_verified_batch` would read
`*FEATURES` for the P99/behaviour rule ablation logic without actually having
the columns loaded, which fails loudly (by design) rather than silently.

Because `model.py`'s `INPUT_COLUMNS`, `encode()`'s
`for column in ['amount_usd_asof', *FEATURES]` log1p loop, and
`inference.py`'s `SCORING_COLUMNS` all already iterate over `FEATURES`
generically, **they pick up the 5 new columns automatically** — no change
needed there beyond this one list growing. This is why this improvement is
implemented before #3 (hyperparameter search): the architecture already
generalizes over `FEATURES`, so this is the cheapest point to add features.

## Step 5 — `infra/terraform/catalog.tf`

The curated Parquet schema is declared once here (`local.curated_columns`,
used by both `smoke_transactions` and `full_transactions` table definitions
via `storage_descriptor.columns`). Add the 5 new columns as `bigint`
(matching the existing 5, which the `full_transactions` table already types
as `bigint` for the behavioural features). This is the one Terraform file
that needs a change — everything else in `infra/` references the Glue
Catalog database/table by name, not by column list.

## Step 6 — `model.py`'s `SavedRanker.queue()` (cosmetic but worth doing)

The human-readable alias dict (`'log1p_txn_count_1h': 'Prior-hour transaction
count (log)'`, etc., built via `zip(FEATURES, [...labels...])`) currently has
exactly 5 labels. `zip` silently truncates to the shorter list, so without an
update the 5 new features would show up in the dashboard's explanation
strings as raw column names (`log1p_in_txn_count_1h: +0.42 margin`) instead of
readable labels. Add 5 more labels, e.g. "Prior-hour incoming count (log)",
"Prior-day/-week incoming count (log)", "Prior-day/-week distinct senders
(log)".

## Step 7 — tests

8 files reference `FEATURES`/`add_behaviour` and need updating:

- `tests/test_analytics.py` — the core correctness tests for `add_behaviour`;
  needs new assertions for the receiver-side windows, including a case where
  sender and receiver windows must *not* leak into each other.
- `tests/test_batch.py`
- `tests/test_model.py` — feature-exclusion/vocabulary tests
- `tests/test_inference.py` — `SCORING_COLUMNS` shape
- `tests/test_ingestion.py`
- `tests/test_dashboard.py`
- `tests/test_full_verification.py`
- `tests/spark/test_glue_parity.py` — the pandas-vs-Spark parity fixture; this
  is the one that actually proves step 2 and step 3 agree. Needs a fixture
  case with multiple senders paying one receiver to exercise the fan-in
  window specifically, not just the existing fan-out cases.

## Step 8 — re-run the training comparison, honestly

Once the feature pipeline is updated end-to-end (Glue parity test green),
re-run `compare_models` from scratch following the project's existing
"frozen before test" discipline — do **not** peek at test metrics and
iterate. This produces a new `selection.json`/`comparison.json`; compare its
validation AP/precision@100 against the current frozen model's validation
numbers (46% / 0.1496) to decide whether to adopt it, using the same gate
logic already in `compare_models`.

## Step 9 — update the docs

`planning/big-data-pipeline-plan.md` §4.3 (feature definitions) and
[the proposal doc](model-performance-improvement-proposal.md) itself (mark #1
as implemented, record the actual before/after metrics once step 8 runs).
