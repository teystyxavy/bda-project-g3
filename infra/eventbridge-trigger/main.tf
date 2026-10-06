variable "enabled" {
  type        = bool
  default     = true
  description = "Rule state; false keeps the stack deployed but ignores uploads."
}
locals {
  account       = "588738581042"
  region        = "ap-southeast-1"
  bucket        = "is459-g3-${local.account}-${local.region}"
  machine       = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-raw-trigger"
  batch         = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-ingest"
  failure_topic = "arn:aws:sns:${local.region}:${local.account}:is459-g3-failures"
  trigger_key   = "pipeline/runs/*/raw/trigger.json"
}

# The bucket had no notification configuration (checked 6 Oct 2026); this stack owns it.
resource "aws_s3_bucket_notification" "eventbridge" {
  bucket      = local.bucket
  eventbridge = true
}
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
resource "aws_iam_role" "events" {
  name               = "is459-g3-raw-trigger-events"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.raw_upload.arn } } }] })
}
resource "aws_iam_role_policy" "events" {
  role   = aws_iam_role.events.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["states:StartExecution"], Resource = local.machine }] })
}
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
resource "aws_iam_role" "workflow" {
  name               = "is459-g3-raw-trigger"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnEquals = { "aws:SourceArn" = local.machine } } }] })
}
resource "aws_iam_role_policy" "workflow" {
  role = aws_iam_role.workflow.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.batch },
    { Effect = "Allow", Action = ["sns:Publish"], Resource = local.failure_topic }
  ] })
}
resource "aws_sfn_state_machine" "raw_trigger" {
  name       = "is459-g3-raw-trigger"
  role_arn   = aws_iam_role.workflow.arn
  type       = "STANDARD"
  definition = templatefile("${path.module}/workflow.asl.json", { batch_arn = local.batch, failure_topic = local.failure_topic })
  depends_on = [aws_iam_role_policy.workflow]
}
output "rule_arn" { value = aws_cloudwatch_event_rule.raw_upload.arn }
output "state_machine_arn" { value = aws_sfn_state_machine.raw_trigger.arn }
