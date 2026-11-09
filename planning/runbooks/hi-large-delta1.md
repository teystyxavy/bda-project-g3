# HI-Large with frozen Delta 1

## Outcome (8 November 2026)

**Status: SUCCEEDED and live on the dashboard.** Run `hi-large-delta1-20261107-batch`
scored all 179,702,229 rows and is the data behind the current dashboard release.

1. **7 Nov:** curate, baseline, score and network passed. The export failed, and a
   retry stopped on one connected component of 26,370 accounts, above the
   19,277-account case limit.
2. **8 Nov:** recovery exports ran on a separate fallback Glue job (see the
   [fallback log](export-fallback-20261108.md)). The lean run `jr_d84e1e93`
   succeeded in 1h34m (about USD 11, two G.8X, peak driver memory 27.2 GB) with exact
   score parity and exact dashboard parity. The oversized component is withheld from
   the actionable rings and kept as private evidence; it is not split or tuned.
3. **8 Nov, 21:33 SGT:** published to the live dashboard with the new UI.
4. **8 Nov, 22:28 SGT:** Step Functions execution
   `hi-large-delta1-20261107-batch-resume1` used `Begin`'s resume mode to skip
   curate, score and network, verified the baseline, adopted the lean export and
   finished `SUCCEEDED` in 5m26s (about USD 0.32). The result cache now returns this
   release instantly for an identical request.
5. **8 Nov, 22:49 SGT:** the improved export became the production export job, so
   future runs get the same fixes.

BQ2 over the full population: 1,927,631 accounts, 9,732 rings (largest 173),
16,315 loops and 156,128 back-and-forth pairs. Machine-readable record:
[hi-large-delta1-result-20261108.json](../evidence/hi-large-delta1-result-20261108.json).

Do **not** redrive the failed 7 Nov execution: its error routes to the Fail state, so
a redrive fails again. Use the resume package in
`output/sfn-hi-large-resume-20261108/ready/` if a resume is ever needed again. Since 9 Nov the
dashboard offers a **HI-Large cache-only** run: it returns this published release in
about 10 seconds with no Glue (`output/hi-large-cached-run-20261109/`).

## Original run plan

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
