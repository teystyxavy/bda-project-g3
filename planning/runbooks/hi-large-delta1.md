# HI-Large with frozen Delta 1

This uses the existing `is459-g3-ingest` Step Functions workflow. It scores all
179,702,229 retained HI-Large transactions and computes BQ2 over that complete
population. BQ1 reports the promoted model's original chronological periods:

| Period | Inclusive dates | Transactions |
|---|---|---:|
| Training reference | 1–10 September 2022 | 17,798,311 |
| Validation | 11–14 September 2022 | 6,623,289 |
| Test | 15 September 2022–12 January 2023 | 95,812,493 |

The source also contains dates before training. Those rows receive scores and
contribute to BQ2, but do not enter validation/test metrics. The 77,266,447 rows
outside evaluation include both training and earlier source dates; they must
not all be described as model training rows.

Test results additionally distinguish 4,993,077 rows on dates already present
in the model's original test report from 90,819,416 rows on dates absent from
model development inputs. This is exploratory synthetic-data evaluation, not
proof of independent production performance. Test results never select or
retune the model.

The pinned model is `hist-depth5-delta1`, weight hash
`0170bef63f4cd257cbcda8c4748cea0dcf90f202134105fb8226d6dde6a0646f`.
The archive originates from the completed SageMaker job
`is459-g3-train-datesubset-verify1`. Promotion retains the original selection,
feature schema, weights, splits and development dates. The comparison report
adds training-subset/promotion provenance; its original hash is retained.
Training is disabled for this ingestion run.

Use `scripts/ingestion/stage-s3-run.py` to prepare the retained raw CSV inputs
without downloading them to a laptop. It uses fresh run IDs, conditional
server-side S3 copies, encryption and file-size/SHA metadata checks. The
standard baseline job subsequently re-verifies the complete raw population.
`--start` requires that the supplied deployment is already active.

Large baseline/export requests use two G.4X/G.8X workers respectively to give
the Python driver memory headroom. Small and Medium requests retain G.2X.
Existing stage timeouts and the twelve-hour workflow deadline remain in force;
paid compute is not automatically retried. The publisher has 1 GiB memory for
the bounded release checks. Existing application-lock and immutable-artifact
retention rules apply: deploy only after the active shared workflow finishes.

No additional Step Functions state machine is needed. Scoring coverage and BQ2
refer to the complete HI-Large run; evaluation windows only filter BQ1 metrics.

Per-stage cache reuse/registration is disabled above 50 million rows because
those outputs exceed the bounded Lambda copy contract. The complete published
result cache remains available for identical subsequent requests. Small and
Medium stage caches retain their existing checks and limits.
