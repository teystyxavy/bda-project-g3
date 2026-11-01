# Proposed Kinesis improvements: incoming scores visible in the dashboard

Status, 1 November 2026: improvement 1 is implemented and tested locally, not
deployed: publisher `src/bda_pipeline/dashboard_streaming.py`, opt-in Terraform in
`infra/streaming` and the Incoming AML view in `dashboard/`. The hosting stack is
unchanged: the snapshot uses a `no-store` object header instead of a new CloudFront
behavior, as `current.json` already does. See the
[streaming README](../../streaming/README.md#incoming-aml-dashboard-view) for deployment.

Status, 6 October 2026: proposed, not implemented or deployed. The verified
streaming replay has 884 results across 32 complete sender histories; its consumer
is disabled. The dashboard serves batch snapshots. This update proposes the
missing serving path, with no cloud activation or new replay.

## Recommended flow

```text
CSV replay → Kinesis → existing scoring Lambda
                           ├→ DynamoDB: unique scores + exact sender history
                           └→ S3: private audit

DynamoDB committed results + reviewed replay metadata
    → dashboard publisher Lambda (once per minute during a bounded demo)
    → S3 dashboard/streaming/<run-id>/snapshot.json
    → existing CloudFront distribution
    → dashboard Incoming AML view (poll every 10 seconds while visible)

Verified full batch → existing batch AML and treasury views
```

Kinesis transports events; the dashboard needs a serving output derived from
committed scores. Reuse the frozen model, feature contract, historical FX and
existing static hosting. The publisher is separate from scoring so dashboard
failures cannot advance sender state, change predictions or block audit repair.

For the first bounded cohort, paginate a strongly consistent read of the existing
run-specific DynamoDB table, select result items and exclude sender-state items.
The current table has only a `pk` key, so this is a bounded scan, not an indexed
queue query. Enforce a reviewed row/byte cap and fail visibly if it is exceeded.
A scan during writes is not a point-in-time snapshot: label running snapshots
provisional, deduplicate by transaction identity and perform final reconciliation
after consumption has stopped. Publish one complete JSON object after validation;
on failure retain the last good object. Use one publisher writer with concurrency
one, a run-scoped destination and bounded invocation duration.

For a larger feed, replace repeated scans with a dedicated serving projection and
queryable run/time keys. DynamoDB Streams is a possible later trigger; it requires
result-item filtering and duplicate-safe projection writes. That expansion is
outside the first dashboard connection.

## What the user sees

- An **Incoming AML — synthetic replay** view with a ranked top-100 transaction
  queue, deterministic transaction-ID tie-breaking and a processed-transaction
  count. Show event timestamp, amount in USD, payment format, score and computed
  behaviour signals. Scores remain rankings, not laundering probabilities.
- Run progress against the reviewed 884-event manifest, last successful update,
  model version and clear states: waiting, running, paused, failed, completed.
  Determine state from run control and acceptance evidence; counts alone do not
  prove completion. Display stale data explicitly after two minutes without a
  successful refresh. A completed replay does not become stale merely by aging.
- Separate historical event time (September 2022) from arrival, score commit and
  publication time. Measure demo freshness using wall-clock times. Proposed
  initial target: visible within 90 seconds under the bounded demo load; measure
  it before claiming success. This cadence does not support a 30-second SLA.
- Signal drilldown explains the observed inputs. Model contribution explanations
  require reconstruction with the frozen artifact and tested parity; do not label
  raw feature values as causal explanations. Do not show live precision/recall
  using labels withheld from the scoring input.

Keep the stream queue separate from the full batch queue. These are replayed rows
already present in the batch dataset: appending them would duplicate transactions.
BQ2 corridor volume, shares and HHI continue to use verified batch aggregates.
A selected-sender replay is not a representative treasury population. A future
genuine incremental feed needs identity/revision reconciliation before combining
its outputs with batch totals.

## Data and hosting changes required

Existing stored results contain identity, historical timestamp, rolling features
and score; they do not retain all display fields or wall-clock processing times.
For the reviewed cohort, join display fields from its immutable input manifest.
For future events, persist an allowlisted display projection and immutable
arrival/commit timestamps, plus model/feature/FX versions. Add these fields through
a versioned contract without changing score inputs or historical window semantics.
Existing acceptance payloads remain historical evidence and must not be rewritten.

The snapshot contract should contain schema version, run ID, cohort scope,
expected/processed counts, top-K scope, queue rows, model digest, generation time,
run state and reconciliation status. Ranking and counts use unique committed
results, not invocation counts or S3 object notifications. A capped top-100 export
must not imply that arbitrary filters search all processed rows.

Publish only allowlisted synthetic demo fields, with masked account identifiers;
keep audit payloads, sender history and model artifacts private. Give the
publisher read-only access to the exact state table and input metadata, and write
access only to the streaming dashboard prefix. The browser fetches through
CloudFront without AWS credentials. Start the timer only for an explicitly
authorized demo, with a fixed end time; stop it when the run ends.

Add a dedicated CloudFront behavior for `/streaming/*` with minimum/default/
maximum TTL zero and use `Cache-Control: no-store` on snapshots. The existing
distribution uses `/dashboard` as its origin path. Browser `cache: 'no-store'`
alone cannot bypass an edge-cached response, and its existing configuration does
not include query strings in the cache key. Avoid per-update invalidations and
timestamp query strings as the freshness mechanism. Keep ordinary assets cached.
See [AWS cache-policy documentation](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cache-key-understand-cache-policy.html).

## Improvements in priority order

1. **Finish event-to-dashboard visibility:** local snapshot builder and sample
   Incoming AML view first; then Terraform publisher/timer and hosting behavior.
   Keep scoring and audit commitments independent of publication.
2. **Measure the whole path:** arrival-to-commit and commit-to-visible delay,
   unique counts, oldest unprocessed arrival and refresh failures. Retain current
   feature/score parity checks. Distinguish paused replay from processing failure.
3. **Improve producer throughput carefully:** the existing 884-row replay took
   1,049.545 seconds despite its ten-event/second cap. Profile acknowledgements
   and reuse connections first. Preserve sender ordinals and order; naive bulk
   retries can deliver a later sender event before a failed earlier event.
4. **Retain reliability controls:** duplicate-safe state/results, audit repair,
   bounded history, first-failed-sequence retry and failure archives. AWS partial
   batch handling can redeliver the suffix from the lowest failed sequence, so
   serving effects must also tolerate duplicates. See [AWS Kinesis retry behavior](https://docs.aws.amazon.com/lambda/latest/dg/services-kinesis-batchfailurereporting.html).
5. **Broader reuse:** patch/review the container findings and repeat runtime parity
   before a new cloud demonstration. Add transaction notifications only after
   defining investigation capacity and duplicate-safe alerting; SNS currently
   reports processing failures.

## Acceptance and costs

Local acceptance: duplicate replay leaves queue/counts unchanged; deterministic
top-K matches committed scores; sender items are excluded; scan pagination is
complete; invalid/over-cap snapshots preserve the prior publication; public JSON
contains only allowed fields. Browser checks cover automatic refresh, stale and
failed states, safe text rendering and an unchanged batch/treasury view.

After separately authorized deployment/replay: observe the queue grow without
manually rebuilding the batch dashboard; reconcile all 884 unique results and
32 sender histories; compare final top-100 with the frozen cohort reference;
measure wall-clock visibility including CloudFront; stop consumer and publisher.
Record read/write requests, invocation duration and hosting requests. Do not
publish a completed/reconciled status until all acceptance checks pass.

A 20-minute demo at one publication/minute is approximately 20 publisher
invocations/writes, plus paginated DynamoDB reads and about 120 browser polls per
viewer at ten-second intervals. These are planning volumes, not measured charges.
The retained provisioned shard remains billable even when consumption is disabled;
the existing reviewed estimate is USD 0.0184/hour, about USD 13.69 per 744 hours.
Preserve the USD 250 course budget and USD 10 monthly alert. Cleanup requires the
user's explicit instruction. No infrastructure deployment, schedule activation,
paid replay or public publication is authorized by this proposal.
