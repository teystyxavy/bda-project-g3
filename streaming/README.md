# Bounded streaming demo

Fourteen resources and the reviewed image are deployed. The one approved replay
passed all 884 result comparisons and 32 sender-history checks. Consumption is
Disabled and the retained infrastructure has no drift. See the
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
output/ml-venv/Scripts/python.exe scripts/prepare-streaming-demo.py --reference output/cloud-insights-20261005/bq1_scored.parquet --artifacts output/cloud-scoring-retry-prepared-20261005/artifacts --output output/streaming-demo-repeat
output/ml-venv/Scripts/python.exe scripts/replay-streaming-demo.py --package output/streaming-demo-repeat
output/ml-venv/Scripts/python.exe -m pytest -q tests/test_streaming.py tests/test_streaming_aws.py tests/test_streaming_verification.py
output/ml-venv/Scripts/python.exe scripts/prepare-streaming-container.py --artifacts output/cloud-scoring-retry-prepared-20261005/artifacts --output output/streaming-container-repeat
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
output/ml-venv/Scripts/python.exe scripts/verify-streaming-demo.py --package output/streaming-demo-20261006 --audit C:/Users/lowth/AppData/Local/IS459-G3/streaming-reconciliation-20261006/audit --receipt C:/Users/lowth/AppData/Local/IS459-G3/streaming-receipt-20261006.jsonl --output output/streaming-cloud-verification-repeat.json
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
