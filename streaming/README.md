# Bounded streaming demo

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

Next proposed improvement: [connect committed Kinesis scores to an automatically
refreshing Incoming AML dashboard](../planning/proposals/kinesis-dashboard-improvements.md).
This serving path is proposed; the verified consumer remains disabled.

Fourteen resources and the reviewed image were deployed; the stream and its
mapping are now parked (above). The one approved replay passed all 884 result
comparisons and 32 sender-history checks. See the
[completed result](../planning/archive/reviews/streaming-demo-result-20261006.md),
[revised review](../planning/archive/reviews/streaming-failure-isolation-review-20261006.md) and
[original deployment review](../planning/archive/reviews/streaming-demo-review-20261006.md)
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
