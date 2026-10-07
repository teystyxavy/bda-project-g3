# EventBridge upload trigger

Implements the proposal's "raw files land in S3; EventBridge starts the workflow".
This stack adds no compute; it starts the existing `is459-g3-ingest` workflow.

Live acceptance passed on **7 October 2026**: run
`evb-smoke-20261007-135938` processed 1,000 rows through verified publication
in 8m27.55s. Re-uploading the same trigger marker started another wrapper that
took `AlreadyStarted`, with exactly one batch execution. The original dashboard
release was restored afterward. See
[acceptance evidence](../../planning/evidence/eventbridge-trigger-result-20261007.json).
The target has bounded delivery retries but no DLQ or dedicated delivery-failure
alarm; these failure-recovery enhancements remain unimplemented. SNS notification
inside the wrapper covers failures after it starts, not delivery failures before it.

```text
laptop: ingest.ps1 -LocalAcquisition -Trigger EventBridge
  -> S3 pipeline/runs/<run>/raw/{transactions,accounts,fx}.csv, manifest.json
  -> S3 pipeline/runs/<run>/raw/trigger.json         (written last)
  -> EventBridge rule is459-g3-raw-upload-trigger   (S3 "Object Created")
  -> Step Functions is459-g3-raw-trigger            (checks the key, names the run)
  -> Step Functions is459-g3-ingest                 (unchanged batch workflow)
```

Only `trigger.json` matches the rule. The manifest uploads made by the direct CLI
path and by cloud acquisition (`is459-g3-cloud-ingest`) never match, so those paths
keep starting the batch themselves and cannot race the trigger. The wrapper names
the batch execution after the run ID, so a redelivered S3 event finds the existing
execution and stops; it never starts a second paid run. It does not retry.

## Resources (8)

| Resource | Purpose |
|---|---|
| `aws_s3_bucket_notification.eventbridge` | Send the data bucket's S3 events to EventBridge (the bucket had no notification configuration) |
| `aws_cloudwatch_event_rule.raw_upload` | Match `pipeline/runs/*/raw/trigger.json` uploads |
| `aws_cloudwatch_event_target.workflow` + role/policy | Start the wrapper only |
| `aws_sfn_state_machine.raw_trigger` + role/policy | Validate the key, start `is459-g3-ingest` once, SNS on failure |

Cost: S3-to-EventBridge delivery and rule matching for AWS service events are free;
the wrapper uses about five Standard state transitions per run.

## Deploy, pause, remove

Run from the repository root. State stays under `%LOCALAPPDATA%/IS459-G3/terraform-eventbridge-trigger`.

```powershell
.\scripts\aws\deploy-eventbridge-trigger.ps1                    # plan only, no AWS writes
.\scripts\aws\deploy-eventbridge-trigger.ps1 -Execute           # apply the eight additions
.\scripts\aws\deploy-eventbridge-trigger.ps1 -Disable -Execute  # keep the stack, ignore uploads
.\scripts\aws\deploy-eventbridge-trigger.ps1 -Destroy           # plan the teardown
.\scripts\aws\deploy-eventbridge-trigger.ps1 -Destroy -Execute  # remove all eight resources
```

Teardown removes the bucket's EventBridge notification setting with the stack. If
another stack later adds S3 notifications to the same bucket, merge them here first,
because AWS keeps one notification configuration per bucket.

## Demo

```powershell
.\scripts\ingestion\ingest.ps1 -LocalAcquisition -Trigger EventBridge -Rows 1000
```

The script uploads, writes `trigger.json`, then waits for EventBridge to start the
batch and follows it like a direct run. A cached identical run still returns the
existing release without uploading.
