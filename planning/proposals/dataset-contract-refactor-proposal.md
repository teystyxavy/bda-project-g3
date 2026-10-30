# Proposal: factor out a single dataset-subset contract

Status: analysis complete, nothing implemented. Triggered by the discovery
(see [model performance proposal](model-performance-improvement-proposal.md)
discussion) that "train on HI-Medium/HI-Large" has no clean answer, because
there is no single place in this codebase that defines what a dataset subset
*is* — the facts are duplicated as literals across many files instead.

## The finding

Grepping `src/bda_pipeline/` for `HI-Small`, `5078345`/`5_078_345`,
`2022-09-01`/`2022-09-18` turns up **18 of 26 files** referencing at least one
of these. They group into five independent fact-types, each duplicated with
no shared source of truth:

| Fact | Duplicated in |
|---|---|
| Subset name `"HI-Small"` as an allow/reject literal | `cloud_acquisition.py` (file allowlist `NAMES`, cache marker filename, `subset` field), `dashboard_api.py` (`if subset != 'HI-Small': raise ApiError(...)`), `dashboard_status.py` (hardcoded in the status response dict), `ingest_contract.validate_manifest` (`subset != 'HI-Small'` reject), `dataset_options.selection_options` (`pipeline_subsets: ['HI-Small']`) |
| Transaction ID prefix `"HI-Small:"` | **Generated** independently in `jobs/curate_transactions.py`'s `parse()` (`f"HI-Small:{index}"`, Spark) and `transform.py`'s `read_transactions` (pandas equivalent); **re-verified** against the same literal string in three more places: `batch.py` (`export_verified_batch`), `cloud_scores.py` (`verify_scores`), `streaming.py` (`validate_event`) |
| Row count `5,078,345` / cap `5,100,000` | `ingest_contract.validate_manifest` (the cap), `cloud_scores.py` (`expected_rows=5_078_345` default), `orchestration.py` (`ROWS = 5_078_345`) |
| Date range `2022-09-01`..`2022-09-18` | **Hard-reject gates**: `jobs/curate_transactions.py`'s `curate()` (`~col("txn_date").between(...)`, raises `"Date outside HI-Small range"`) and, separately, `treasury_verification.py` (`if not frame.txn_date.between('2022-09-01', '2022-09-18').all(): raise ...`). **Chronological split boundaries**: `model.py`'s `SPLITS` (`train`/`validation`/`test`/`tail`) and `batch.py`'s `PERIODS` (`validation`/`test`/`tail`) are two independent dicts with overlapping, hand-kept-in-sync date tuples |
| Raw filenames `HI-Small_Trans.csv` / `HI-Small_accounts.csv` | Hardcoded literally in `cloud_acquisition.py`'s `NAMES = [...]`, duplicating what `dataset_options.py`'s `FILE_SUFFIXES`-based `selected_files()` already computes generically — two independent ways to build the same filename coexist in this codebase |

**What's already done right, as a model to extend:** `dataset_options.py`'s
`SUBSETS`/`FILE_SUFFIXES`/`resolve_subset()`/`selected_files()` is a genuinely
clean single source of truth for *file selection*, already shared correctly
by `kaggle-download.py`, `ingest.py`, and `acquisition.acquire_dataset`. The
gap is that this pattern was never extended past file *naming* into the
pipeline facts above (dates, row counts, ID prefix, allow/reject gates).

## Why this matters beyond "can we train on a bigger file"

Five independently-maintained copies of the same fact means there is no
mechanism that would catch a partial update. Widen the date gate in
`curate_transactions.py` and forget `treasury_verification.py`, and BQ2
verification fails on data that curation just accepted — a confusing failure
far from the actual change, discovered at runtime instead of at review time.
This is exactly the shape of bug that's easy to introduce and hard to
diagnose, and it will recur on every future touch of any of these five facts
— not just a hypothetical Medium/Large migration.

## Proposed refactor: one `SubsetProfile` per subset, everything else derives from it

Add a single module (extending `dataset_options.py` rather than creating a
competing one, since its naming logic is already correct and should stay the
anchor) exposing one frozen object per subset:

```python
# dataset_options.py, additions

from dataclasses import dataclass

@dataclass(frozen=True)
class SubsetProfile:
    name: str                      # 'HI-Small'
    id_prefix: str                 # 'HI-Small' (used as f'{id_prefix}:{row}')
    expected_rows: int | None      # 5_078_345, or None if not yet measured/frozen
    max_rows: int                  # validation cap; independent of expected_rows
    date_start: str                # '2022-09-01'
    date_end: str                  # '2022-09-18' (inclusive)
    pipeline_supported: bool       # False for everything except HI-Small today

# One entry per subset. Only HI-Small has pipeline_supported=True until each
# consuming file below is migrated and a new entry is reviewed and added.
PROFILES = {
    'HI-Small': SubsetProfile('HI-Small', 'HI-Small', 5_078_345, 5_100_000,
                              '2022-09-01', '2022-09-18', pipeline_supported=True),
    # 'HI-Medium': SubsetProfile(..., pipeline_supported=False),  # measured, not yet wired
    # ... remaining four, added as each is actually validated end-to-end
}

def profile(subset):
    """The single lookup every pipeline-fact consumer should use instead of a literal."""
    resolved = resolve_subset(subset)
    if resolved not in PROFILES:
        raise ValueError(f'No reviewed profile for subset {resolved}')
    return PROFILES[resolved]
```

Chronological splits become a **function of the profile's date range**,
not a separately-hardcoded dict:

```python
def chronological_splits(profile, train_days=6, validation_days=2, test_days=2):
    """Replaces model.py's SPLITS and batch.py's PERIODS with one derivation."""
    start = pd.Timestamp(profile.date_start)
    end = pd.Timestamp(profile.date_end) + pd.Timedelta(days=1)  # exclusive end
    train_end = start + pd.Timedelta(days=train_days)
    validation_end = train_end + pd.Timedelta(days=validation_days)
    test_end = validation_end + pd.Timedelta(days=test_days)
    return {'train': (start, train_end), 'validation': (train_end, validation_end),
            'test': (validation_end, test_end), 'tail': (test_end, end)}
```
(This specific day-block shape still only suits an 18-28 day dataset like
Small/Medium — HI-Large's 5.5-month span needs a genuinely different split
*strategy*, as already discussed; the point here is that whatever strategy is
chosen lives in **one function**, not two hand-synced dicts.)

## What each consuming file changes to

| File | Before | After |
|---|---|---|
| `cloud_acquisition.py` | `NAMES = ['HI-Small_Trans.csv', 'HI-Small_accounts.csv']` | `NAMES = dataset_options.selected_files(subset)` |
| `dashboard_api.py` | `if subset != 'HI-Small': raise ApiError(...)` | `if not dataset_options.profile(subset).pipeline_supported: raise ApiError(...)` |
| `ingest_contract.py` | `if value.get('subset') != 'HI-Small'` + literal `5_100_000` | `p = dataset_options.profile(value.get('subset')); if not p.pipeline_supported or not (0 < value['rows'] <= p.max_rows)` |
| `jobs/curate_transactions.py` | `f"HI-Small:{index}"`; `between("2022-09-01", "2022-09-18")` | `f"{profile.id_prefix}:{index}"`; `between(profile.date_start, profile.date_end)` (profile passed as a job argument, resolved from `--subset`) |
| `transform.py` | same ID-prefix literal | same `profile.id_prefix` substitution |
| `batch.py`, `cloud_scores.py`, `streaming.py` | three independent `'HI-Small:' + ...` re-checks | all three import the same `profile.id_prefix` |
| `treasury_verification.py` | second independent date-range gate | `profile.date_start`/`profile.date_end` |
| `orchestration.py` | `ROWS = 5_078_345` | `dataset_options.profile(subset).expected_rows` |
| `model.py` `SPLITS`, `batch.py` `PERIODS` | two hand-synced dicts | both call `chronological_splits(profile)` |

## Honest scope assessment

This is a real, multi-file refactor (10 files touched, several of them —
the Glue job, the control Lambda's API, the training contract — are
currently-working, already-deployed production code paths), not a quick
cleanup. It needs:

- Every one of those files to gain a `subset`/`profile` parameter where it
  currently has none (several are pure functions today with no such input at
  all — e.g. `curate()` has no subset concept whatsoever right now).
- Careful migration so the already-deployed AWS pipeline doesn't regress —
  this touches the same Glue job and Lambda the production dashboard depends
  on.
- Deciding the chronological-split *strategy* question for anything beyond
  Small/Medium-shaped date ranges (a design decision, not a mechanical change
  — see the note under "Proposed refactor" above).

**Not a blocker for the HI-Small retrain in progress.** Everything currently
hardcoded is hardcoded *correctly* for HI-Small specifically — this refactor
doesn't fix a bug in today's behavior, it removes the cost of *changing*
dataset scope later. I'd sequence it as: finish the HI-Small fan-in
validation (local, then SageMaker) first, since that result is what tells you
whether investing in broader dataset support is worth doing at all; do this
refactor only once Medium/Large (or any other subset change) is an actual
near-term plan, not speculatively now.
