# HI-Medium with frozen Delta 1

The 7 November 2026 evaluation pins `hist-depth5-delta1`, SHA-256
`0170bef63f4cd257cbcda8c4748cea0dcf90f202134105fb8226d6dde6a0646f`.
This is a run-specific model deployment; it does not promote or replace the
shared pipeline or streaming model.

| Output | Population |
|---|---|
| BQ1 model and rule evaluation | 4,959,272 HI-Medium transactions, 15–28 September 2022 |
| BQ2 network analysis | All 31,898,238 HI-Medium transactions, 1–28 September 2022 |

Delta 1's model report records training on 1–10 September and validation on
11–14 September. Neither window enters BQ1 evaluation. The original model
input included 15–16 September as test dates; these are reported separately
from 17–28 September, which were absent from the development input. The latter
contain only 6,987 transactions and are a sparse synthetic tail, so they must
not be treated as a representative production holdout. Synthetic datasets may
also share generation patterns; excluding dates is not proof of independence.

Features retain the verified earlier source history. Rule thresholds come from
the immutable training snapshot; evaluation rows do not fit thresholds, train
a model, select a model, or retune it.

`scripts/ingestion/run-medium-delta1.py` verifies the original successful Glue
stages and source proof, copies only selected curated partitions and the full
network tables into a fresh run, uploads content-addressed code/model assets,
and starts one isolated Glue job. This starts paid AWS processing. Its role
can write only that run's outputs and immutable dashboard release. It cannot
change the main dashboard pointer, shared pipeline deployment or pipeline lock.
There are no automatic paid-job retries.

`jobs/medium_delta1_evaluation.py` builds the independent rule reference, runs
Spark scoring with the pinned model, verifies every selected model score, and
recomputes BQ2 against the full original curated source. All source identities,
row/label/USD totals, model hashes, feature fingerprints, network reconciliation
and release checks must pass before publication. A successful run publishes at
`https://d1n61tlw9pcrog.cloudfront.net/releases/<run_id>/index.html`.

The submission record is in
`planning/evidence/hi-medium-delta1-run-20261107.json`. Final result evidence is
at `pipeline/runs/<run_id>/evidence/result.json` in the project data bucket.
Submission alone does not establish success or publication.
