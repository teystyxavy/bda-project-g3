# Bounded streaming demo

## delta1 model with all 15 behaviour features (`stream-delta1-20261107`)

Status, 9 November 2026: **deployed, replayed once, live on the dashboard, stream
parked.** On 7 Nov the delta1 stack was deployed under its own state
(`streaming-delta1-20261107`) and one replay of 984 events finished at 15:51 UTC. All
984 results reconciled: features exact, maximum score difference 9.3e-10, 1,104
DynamoDB items, 984 audit objects, no failures
([evidence](../planning/evidence/streaming-delta1-result-20261107.json)). The
dashboard's **Incoming AML** tab now reads `streaming-config.json` →
`stream-delta1-20261107`. No Kinesis stream exists in the account (checked 9 Nov), so
the stream is parked again. The image scan reports 19 HIGH, 25 MEDIUM, 9 LOW and 1
undefined findings.

The batch pipeline now serves delta1 (model `0170bef6…`, manifest `0b9ba30d…`, trained
on an HI-Large date subset). Release `medium-delta1-20261107-183216-ui1` scores HI-Medium
Sep 15–28: 99 laundering cases in the top 100, AP 0.691. On the HI-Small test days it
reaches 99 of the top 100 and AP 0.461, against 91 and 0.277 for the previous model.
delta1 reads every batch behaviour feature, so the consumer now computes all 15:

- **Exact batch scan.** `scan_step` repeats `curate_transactions._windowed_counts` one
  row at a time, in (event time, source row) order:
  - When time moves forward, entries older than each window are evicted, with each
    USD sum decremented and floored at 0, and a snapshot is taken.
  - Rows sharing a timestamp reuse that snapshot, so they exclude each other.
  - The row is added after its values are taken.

  The queues and running sums live in state, so the floating-point USD sums match batch
  bit for bit. `corridor_prior_count` is the all-time count of earlier payments from the
  sender to the same receiver (`_prior_pair_counts`). Unlike the window features, it
  counts earlier rows that share the timestamp.
- **Sender state** holds the sender's scan and per-receiver pair counts. The replay
  contains each selected sender's whole history.
- **Receiver state** is keyed `<run>:receiver:<hash>`. A receiver is also paid by senders
  outside the replay. Those payments come from the same batch data and are preloaded
  once at version 0 as `pending` rows (`receivers.jsonl`, `seed-streaming-receivers.py`).
  Each replayed payment first applies the pending rows before it, so the receiver's scan
  reaches exactly where batch was. The consumer refuses:
  - a payment to a receiver that was not preloaded;
  - any event at or before the last position already applied.

  The stream has one shard, one concurrent batch and is sent in (event time, source row)
  order. A stream with more shards would need a different design.
- **Commits:** sender state, receiver state and the result commit in one DynamoDB
  transaction. A preloaded receiver is conditioned on its version, not on not existing.
- **Event checks:** they accept `HI-Small`, `HI-Medium` and `HI-Large` identities whose
  number matches the source row, dated 1 September 2022 to 31 January 2023.

Local evidence (`output/streaming-demo-delta1-20261107/verification.json`, no AWS calls):

- **Reference:** the release is a date subset, so its features were computed over the
  full-source curated run `20261107-072313-79997f49` (HI-Medium Sep 1–28, 31,898,238
  rows). The check uses that folder; its digest of per-file SHA-256 values is `d1b88d99…`.
  On 200,000 sampled rows its features equal the release's `bq1_scored.parquet`
  (`f914ecb9…`).
- **Cohort:** 32 senders with 20–40 payments each, 984 transactions from Sep 1 to 16
  (158 of them inside the release's Sep 15–28 window), paying 88 receivers. The receiver
  preload holds 1,957 payments, and the largest state is 11 KB.
- **Parity:** all 15 features are exact, USD sums included. The maximum score difference
  is 0.0.
- **Scores:** none of the 984 is labelled laundering, and none reaches delta1's batch
  top-100 cutoff (0.9954, HI-Medium Sep 15–28). The highest is 0.712.

Deployment uses its own Terraform state, so the 6 October stack is never planned
against. It also uses its own ECR repository, because a repository policy names one
function. It reuses the isolated failure bucket, where each mapping writes under its
own UUID. Every step needs explicit approval:

1. Initialise `infra/streaming` with `infra/shared.s3.tfbackend`, key
   `streaming-delta1-20261107/terraform.tfstate`, and a separate `TF_DATA_DIR`. Use the
   variables in `%LOCALAPPDATA%/IS459-G3/streaming-delta1-20261107.tfvars.json`:
   `run_id=stream-delta1-20261107`, `repository_name=is459-g3-streaming-delta1`,
   `use_existing_failure_bucket=true`, `provision_streaming=true` and
   `model_manifest_sha256=0b9ba30d77237c9f71a1799235316d9f6dfba042dc70c1c31546513da0ea4b6a`.
   The first apply creates the repository, table, roles, logs and alarm.
2. Build `output/streaming-container-delta1-20261107`
   (`prepare-streaming-container.py --artifacts output/model-delta1-deployed --manifest-sha256 0b9ba30d…`).
   Push it to the new repository and review its scan.
3. Apply with `image_uri=<repository>@sha256:<digest>` and `provision_stream=true`.
   This creates the function and a disabled mapping.
4. Preload the receivers: `seed-streaming-receivers.py --package output/streaming-demo-delta1-20261107 --execute-approved`.
   Upload `events.jsonl` to `streaming/stream-delta1-20261107/input/events.jsonl`, then
   run the preflight (`collect-streaming-evidence.py --run-id stream-delta1-20261107 --image <digest> --package …`).
5. Apply `enable_consumer=true replay_approved=true`. Add the publisher
   (`provision_publisher`, `enable_publisher`, `publisher_end_time`) as in the steps below.
   Replay at 10 events/s (about 100 s), then disable the consumer and reconcile.
6. Record the cloud acceptance in `dashboard_streaming.VERIFIED`. Publish
   `streaming-config.json` for the new run, then park the stream.

## Parked

Status, 6 October 2026: **parked to stop shard charges** (about USD 13.69/month).
The Kinesis stream `is459-g3-stream-smoke-20261006` and its disabled Lambda event
source mapping were deleted. Everything else is kept, so nothing needs rebuilding:
the ECR image (`sha256:7430b609...`), the Lambda function, the DynamoDB state table
(916 items, deletion-protected), the failure bucket, the S3 audit objects and the
alarm. The batch pipeline and dashboard do not use any of it.

Terraform: `provision_stream` (default `false`) gates only the stream and mapping.
The saved variables file has no `provision_stream` key, so applying it as before
keeps the stream parked. The stream was removed directly in AWS, so the first plan
after parking refreshes it out of state and should report no changes. Initialise
with the same private `TF_DATA_DIR` and state path under `%LOCALAPPDATA%/IS459-G3`
that the original deployment used, then:

```powershell
terraform -chdir=infra/streaming plan -var-file="$env:LOCALAPPDATA/IS459-G3/streaming-retained-disabled.tfvars.json"
```

**Restore** (recreates one shard with the same name; the consumer stays disabled):

```powershell
terraform -chdir=infra/streaming apply -var-file="$env:LOCALAPPDATA/IS459-G3/streaming-retained-disabled.tfvars.json" -var provision_stream=true
```

Then follow the replay steps below. A restored stream starts empty, so a demo needs a
fresh replay. Keep the same `run_id`: changing it renames every streaming resource.
Park it again by applying without `-var provision_stream=true`.

## Incoming AML dashboard view

Status, 9 November 2026: the tab now serves the delta1 run `stream-delta1-20261107`
(see the top of this file). The 7 November notes below describe the first
publication, for `stream-smoke-20261006`.

Status, 7 November 2026: **publisher deployed and tab published.** Steps 1–5 below are
done for `stream-smoke-20261006`. The publisher stack added 9 resources, changed 0 and
destroyed 0, with the schedule disabled. One publication returned `completed`: 884/884,
with all 32 sender histories reconciled. `dashboard/streaming-config.json` was published
on 7 November, so CloudFront shows the Incoming tab. It serves the snapshot with
`Cache-Control: no-store` (edge miss). Step 6 (a live window) has not been run. The
deployed publisher predates the review-cutoff and parity fields, so the live snapshot
does not show them yet.

The input `events.jsonl` (SHA-256 `8f5e2aee…e296f`) was rebuilt from the verified
6 October curated data, because the original package was not in AWS. All 884 events
hash to the `input_sha256` the consumer stored when scoring them. To plan this stack
on another machine, run `scripts/aws/get-terraform-inputs.ps1 -Stack streaming`; a plan
with only its `-var-file` should report no changes.

Committed streaming scores now have a serving path to the dashboard, following the
[proposal](../planning/proposals/kinesis-dashboard-improvements.md):

```text
DynamoDB committed results (consistent scan) + reviewed events.jsonl (pinned SHA-256)
  -> publisher Lambda (src/bda_pipeline/dashboard_streaming.py), every 15 s while scheduled
  -> s3://is459-g3-dashboard-.../dashboard/streaming/<run_id>/snapshot.json (no-store)
  -> CloudFront /streaming/<run_id>/snapshot.json -> "Incoming AML · live" tab (polls every 10 s)
```

The publisher is separate from the consumer: it cannot score, replay or change
sender state. It writes a snapshot only after every committed result matches the
reviewed replay input (identity, input digest, five sender features, score range);
otherwise the last good snapshot stays and the browser labels it stale after two
minutes. Completion requires every event and every sender's final version, not counts.
Wall-clock scoring times come from the S3 write times of the audit copies, which the
consumer writes right after each commit. A single publication of a finished run
therefore still shows its whole timeline. The 6 Oct replay shows 884 scores between
00:11:14 and 00:28:20 UTC.
States: waiting, running, paused (consumer disabled), completed, failed (record-failure
alarm). Accounts are masked; labels are never used. The tab is hidden until
`streaming-config.json` is published, so the batch views are unchanged without it.

Deployment (requires explicit authorization; all steps use existing names):

1. Package: `python scripts/aws/prepare-dashboard-streaming.py --output output/dashboard-streaming-package`.
2. Upload the reviewed package's `events.jsonl` unchanged to
   `s3://is459-g3-588738581042-ap-southeast-1/streaming/<run_id>/input/events.jsonl`.
   Its SHA-256 must equal `events_sha256` in that package's `verification.json`.
3. Plan and apply `infra/streaming` with the saved variables plus
   `-var provision_publisher=true -var lambda_zip_path=<absolute zip path> -var replay_events_sha256=<events_sha256>`.
   This adds the publisher, its role, log group, disabled schedule and error alarm;
   check the plan changes nothing else. Review the `publisher_policy` output.
4. Publish the `publisher_configuration` output as `dashboard/streaming-config.json`
   (content type `application/json`, cache control `no-store,max-age=0`) and invalidate
   `/streaming-config.json`. Publish the updated `index.html`, `dashboard.js` and
   `dashboard.css` and update the processing image's bundled assets, exactly as in
   [dashboard-status](../infra/dashboard-status/README.md) steps 3–4.
5. Show the existing replay without any Kinesis cost: invoke the publisher once with
   payload `{"once": true}`. The retained 884 results should publish as completed.
6. For a live demo window, also apply `-var enable_publisher=true -var publisher_end_time=<UTC YYYY-MM-DDTHH:MM:SS>`
   with the stream and consumer restored. The schedule stops at the end time; park it
   again by applying without `enable_publisher`.

The snapshot relies on its `no-store` object header and the distribution's zero minimum
TTL to bypass edge caching, the same mechanism `current.json` uses; the hosting stack is
unchanged. Planning cost for a 20-minute window: about 80 snapshot writes, scans of
under 1,000 items and audit listings, plus about 120 browser polls per viewer. These are estimates, not
measured charges.

Replaying the same cohort into the same `run_id` adds nothing new: results are
idempotent, so the queue would show 884 of 884 at once. Seeing the queue grow live
needs a fresh replay namespace. A new `run_id` renames every resource in this stack,
and the retained table is deletion-protected, so plan that change separately.

Fourteen resources and the reviewed image were deployed; the stream and its
mapping are now parked (above). The one approved replay passed all 884 result
comparisons and 32 sender-history checks. See the
completed result (archived),
revised review (archived) and
original deployment review (archived)
for evidence, costs, exact approval scope and cloud acceptance criteria.

The producer replays verified curated CSV transactions; Lambda recomputes exact
preceding sender-history features and uses the frozen model. Conditional
DynamoDB transactions persist state and unique results, followed by S3 audit.
Bank/account composite keys, original event times and per-sender ordinals
preserve the batch semantics. Labels and precomputed rolling features are
rejected. Missing/out-of-order events or exhausted state limits stop scoring.
Duplicates repair missing audit without double-counting. SNS is for processing
failures; no per-transaction alert threshold is configured.

## Reproduce local preparation

Run from the repository root with the existing ML environment. Choose fresh
output paths rather than overwriting reviewed packages:

```powershell
output/ml-venv/Scripts/python.exe scripts/streaming/prepare-streaming-demo.py --reference output/cloud-insights-20261005/bq1_scored.parquet --artifacts output/cloud-scoring-retry-prepared-20261005/artifacts --output output/streaming-demo-repeat
output/ml-venv/Scripts/python.exe scripts/streaming/replay-streaming-demo.py --package output/streaming-demo-repeat
output/ml-venv/Scripts/python.exe -m pytest -q tests/test_streaming.py tests/test_streaming_aws.py tests/test_streaming_verification.py
output/ml-venv/Scripts/python.exe scripts/streaming/prepare-streaming-container.py --artifacts output/cloud-scoring-retry-prepared-20261005/artifacts --output output/streaming-container-repeat
docker build --platform linux/amd64 --provenance=false -t is459-g3-streaming:repeat output/streaming-container-repeat
```

Preparation checks every selected event against the full frozen reference and
then duplicate-replays it. The producer's default is a dry run with no AWS
calls. Docker builds pull the official base and dependencies but do not push
images or create AWS resources. A rebuilt image gets its own digest and review;
the current reviewed image is `is459-g3-streaming:final-20261006`.

Infrastructure source is `infra/streaming/`. Defaults create no resources.
The additional failure bucket is also opt-in (`provision_failure_bucket`); it
was deployed under the approved revised plan. Existing streaming
resources require explicit current variables to retain them in an apply; do
not apply the default-off configuration to a populated stack.
Do not apply or add `--execute-approved` before explicit cloud authorization.
After authorization, stage resources, verify the uploaded image and scan, create
the disabled function/mapping, activate it, send the one reviewed cohort and
disable the consumer after acceptance. Use a fresh receipt outside OneDrive;
an interrupted receipt needs reconciliation before any resumed send.

For retained cloud output after an approved run:

```powershell
output/ml-venv/Scripts/python.exe scripts/streaming/verify-streaming-demo.py --package output/streaming-demo-20261006 --audit C:/Users/lowth/AppData/Local/IS459-G3/streaming-reconciliation-20261006/audit --receipt C:/Users/lowth/AppData/Local/IS459-G3/streaming-receipt-20261006.jsonl --output output/streaming-cloud-verification-repeat.json
```

This acceptance command only reads local files. Its passing report verifies
audit parity and receipt completeness; inspect sender versions, mapping/Lambda
errors and failure archives separately. A successful producer send alone does
not prove scoring succeeded. Disabling consumption leaves a billable shard;
cleanup requires explicit approval.

The saved current variables are `%LOCALAPPDATA%/IS459-G3/streaming-retained-disabled.tfvars.json`.
They retain both resource flags, the exact image digest and disabled consumption.
No further cloud replay is authorized. The original image has 8 HIGH, 6 MEDIUM
and 1 LOW OS-package scan findings; patch and review it before broader reuse.
