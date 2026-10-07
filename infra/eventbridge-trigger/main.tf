# =============================================================================
# infra/eventbridge-trigger/main.tf -- start the pipeline when raw data lands
# =============================================================================
# What it is: the event-driven entry point of the batch pipeline. When an
# upload to s3://<bucket>/pipeline/runs/<run_id>/raw/trigger.json completes,
# S3 emits an "Object Created" event to EventBridge, a rule matches it and
# starts a small wrapper state machine (is459-g3-raw-trigger). The wrapper
# (definition in workflow.asl.json, same folder) starts the main ingestion
# workflow is459-g3-ingest (infra/ingestion) for that run, and publishes to
# the shared SNS failure topic if it cannot.
#
# Why trigger.json and not the CSVs? The uploader (scripts/ingestion/ingest.py
# with --trigger eventbridge) writes trigger.json LAST, after every raw file
# and manifest.json, so the pipeline never starts on a half-uploaded run.
#
# Deployed with scripts/aws/deploy-eventbridge-trigger.ps1 (-Disable sets
# enabled = false to pause the trigger without destroying anything).
#
# -----------------------------------------------------------------------------
# variable "enabled": rule on/off switch. false keeps every resource deployed
# but the rule ignores uploads (runs can still be started directly).

variable "enabled" {
  type        = bool
  default     = true
  description = "Rule state; false keeps the stack deployed but ignores uploads."
}
# Fixed names/ARNs. machine = the wrapper state machine created below;
# batch = the main ingestion workflow it starts; trigger_key = wildcard for
# the per-run trigger object.
locals {
  account       = "588738581042"
  region        = "ap-southeast-1"
  bucket        = "is459-g3-${local.account}-${local.region}"
  machine       = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-raw-trigger"
  batch         = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-ingest"
  failure_topic = "arn:aws:sns:${local.region}:${local.account}:is459-g3-failures"
  trigger_key   = "pipeline/runs/*/raw/trigger.json"
}

# Turns on "send all S3 events of this bucket to EventBridge". Without it the
# rule below would never receive Object Created events. NOTE: a bucket has a
# single notification configuration, so this resource owns it entirely.
# The bucket had no notification configuration (checked 6 Oct 2026); this stack owns it.
resource "aws_s3_bucket_notification" "eventbridge" {
  bucket      = local.bucket
  eventbridge = true
}
# EventBridge rule: match only "Object Created" events from S3 for our bucket
# whose key matches pipeline/runs/*/raw/trigger.json (wildcard matching).
# Uploads of other files (CSVs, manifest) are ignored.
resource "aws_cloudwatch_event_rule" "raw_upload" {
  name        = "is459-g3-raw-upload-trigger"
  description = "Start is459-g3-ingest when an upload finishes with pipeline/runs/<run>/raw/trigger.json."
  state       = var.enabled ? "ENABLED" : "DISABLED"
  event_pattern = jsonencode({
    source        = ["aws.s3"]
    "detail-type" = ["Object Created"]
    detail = {
      bucket = { name = [local.bucket] }
      object = { key = [{ wildcard = local.trigger_key }] }
    }
  })
}
# Role EventBridge assumes to call the target. Trusted only for this account
# and this specific rule (aws:SourceArn) -- confused-deputy protection.
resource "aws_iam_role" "events" {
  name               = "is459-g3-raw-trigger-events"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.raw_upload.arn } } }] })
}
# Its only permission: start executions of the wrapper state machine.
resource "aws_iam_role_policy" "events" {
  role   = aws_iam_role.events.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["states:StartExecution"], Resource = local.machine }] })
}
# Target: the wrapper state machine receives the S3 event as its input.
# EventBridge retries delivery up to 3 times within 1 hour if the start fails.
resource "aws_cloudwatch_event_target" "workflow" {
  rule     = aws_cloudwatch_event_rule.raw_upload.name
  arn      = aws_sfn_state_machine.raw_trigger.arn
  role_arn = aws_iam_role.events.arn
  # A redelivered event only reaches the wrapper, which starts the batch under the run ID once.
  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 3
  }
}
# Role for the wrapper state machine, trusted only for that machine.
resource "aws_iam_role" "workflow" {
  name               = "is459-g3-raw-trigger"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnEquals = { "aws:SourceArn" = local.machine } } }] })
}
# Wrapper permissions: start the main ingestion workflow (is459-g3-ingest) and
# publish to the failure topic if starting it fails.
resource "aws_iam_role_policy" "workflow" {
  role = aws_iam_role.workflow.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.batch },
    { Effect = "Allow", Action = ["sns:Publish"], Resource = local.failure_topic }
  ] })
}
# The wrapper state machine. Its JSON definition (workflow.asl.json) gets the
# batch ARN and failure topic substituted by templatefile(). Using a wrapper
# rather than targeting the batch directly lets it derive the run id from the
# S3 key and start the batch under that id once (duplicate events are safe).
resource "aws_sfn_state_machine" "raw_trigger" {
  name       = "is459-g3-raw-trigger"
  role_arn   = aws_iam_role.workflow.arn
  type       = "STANDARD"
  definition = templatefile("${path.module}/workflow.asl.json", { batch_arn = local.batch, failure_topic = local.failure_topic })
  depends_on = [aws_iam_role_policy.workflow]
}
# Outputs for verification scripts: the rule ARN and the wrapper's ARN.
output "rule_arn" { value = aws_cloudwatch_event_rule.raw_upload.arn }
output "state_machine_arn" { value = aws_sfn_state_machine.raw_trigger.arn }
