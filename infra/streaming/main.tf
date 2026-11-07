# =============================================================================
# infra/streaming/main.tf -- OPTIONAL real-time streaming demo
# =============================================================================
# What it is: a small, separately-gated demonstration that the frozen AML
# model can score transactions one at a time as they arrive, instead of only
# in the nightly Glue batch. Flow:
#   replay producer (scripts/streaming/replay-streaming-demo.py)
#     -> Kinesis Data Stream (1 shard)
#     -> Lambda consumer (container image built from streaming/Dockerfile,
#        code in streaming/handler.py + src/bda_pipeline/streaming.py)
#     -> DynamoDB table (per-key state + per-record results, de-duplicated)
#     -> S3 audit files under s3://<pipeline bucket>/streaming/<run_id>/audit/
# Records the consumer cannot process are sent by the event-source mapping to
# an isolated "stream-failures" S3 bucket, and a CloudWatch metric/alarm turns
# "stream_record_failed" log lines into an SNS alert.
#
# Everything is OFF by default (see variables.tf): provision_streaming creates
# the cheap pieces, provision_stream adds the billable Kinesis shard,
# enable_consumer actually connects Lambda to the stream (requires explicit
# replay approval). This keeps the demo from costing money while idle.

locals {
  # Feature switches converted to resource counts (count = 0 means "not created"):
  #   count          -- base streaming resources (ECR repo, table, role, logs);
  #   function_count -- the Lambda only exists once an image digest is supplied;
  #   stream_count   -- the Kinesis shard (the only always-billing component);
  #   publisher_count -- the dashboard publisher (end of this file).
  # Names: every per-run resource is called is459-g3-<run_id>, so a fresh replay
  # run gets fresh, isolated resources. The *_arn values are pre-computed so the
  # IAM runtime policy can name exact resources even before they exist.
  count          = var.provision_streaming ? 1 : 0
  function_count = var.provision_streaming && var.image_uri != null ? 1 : 0
  # The shard is the only resource that bills while idle; provision_stream parks it.
  stream_count    = var.provision_streaming && var.provision_stream ? 1 : 0
  publisher_count = var.provision_publisher ? 1 : 0
  name            = "is459-g3-${var.run_id}"
  bucket          = "is459-g3-${var.aws_account_id}-${var.aws_region}"
  failure_bucket  = "is459-g3-stream-failures-${var.aws_account_id}-${var.aws_region}"
  site_bucket     = "is459-g3-dashboard-${var.aws_account_id}-${var.aws_region}"
  stream_arn      = "arn:aws:kinesis:${var.aws_region}:${var.aws_account_id}:stream/${local.name}"
  table_arn       = "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${local.name}"
  log_arn         = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${local.name}"
  # Frozen model manifest baked into the reviewed consumer image.
  model_manifest = var.model_manifest_sha256
  # Failed records go to the failure bucket this stack creates, or to the one another streaming state manages.
  failure_destination = var.provision_failure_bucket || var.use_existing_failure_bucket
  # Exact names the publisher policy template may reference.
  publisher_policy_inputs = {
    region      = var.aws_region
    account     = var.aws_account_id
    name        = local.name
    bucket      = local.bucket
    site_bucket = local.site_bucket
    run_id      = var.run_id
  }
}
# Private ECR repository holding the consumer's container image. IMMUTABLE tags
# mean a pushed tag can never be overwritten (the Lambda is pinned by digest
# anyway); images are scanned for known vulnerabilities on push and encrypted.
resource "aws_ecr_repository" "runtime" {
  count                = local.count
  name                 = var.repository_name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  encryption_configuration { encryption_type = "AES256" }
  image_scanning_configuration { scan_on_push = true }
}
# The Kinesis Data Stream the replay producer writes transactions into.
# 1 provisioned shard (~1 MB/s in) is plenty for a demo; data is kept for the
# minimum 24 hours; records are encrypted with the AWS-managed Kinesis KMS key.
resource "aws_kinesis_stream" "replay" {
  count            = local.stream_count
  name             = local.name
  shard_count      = 1
  retention_period = 24
  encryption_type  = "KMS"
  kms_key_id       = "alias/aws/kinesis"
  stream_mode_details { stream_mode = "PROVISIONED" }
}
# DynamoDB table for the consumer's durable state. Single string key "pk" holds
# both per-entity state items (with a version number for optimistic locking)
# and per-record result items, so a redelivered Kinesis record is detected and
# not scored twice. On-demand billing = pay per request, nothing while idle.
# Deletion protection and encryption at rest are on.
resource "aws_dynamodb_table" "state" {
  count        = local.count
  name         = local.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  deletion_protection_enabled = true
  server_side_encryption { enabled = true }
}
# Lambda's log group, pre-created so logs expire after 7 days and so the
# metric filter below can attach to it.
resource "aws_cloudwatch_log_group" "consumer" {
  count             = local.count
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 7
}
# Execution role for the Lambda consumer; only the Lambda service may assume it.
resource "aws_iam_role" "consumer" {
  count              = local.count
  name               = local.name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
# Runtime policy is generated/reviewed separately from deployment credentials.
# The role's permissions come from runtime-policy.json.tftpl (rendered with
# this run's names) so they can be reviewed separately. They cover reading the
# Kinesis stream, DynamoDB state, writing audit/failure objects and logs.
resource "aws_iam_role_policy" "consumer" {
  count  = local.count
  role   = aws_iam_role.consumer[0].id
  policy = templatefile("${path.module}/runtime-policy.json.tftpl", { region = var.aws_region, account = var.aws_account_id, name = local.name, bucket = local.bucket, failure_bucket = local.failure_bucket, run_id = var.run_id })
}
# The consumer function. Packaged as a container image pinned by sha256 digest
# (var.image_uri). 2 GB memory / 60 s timeout for loading the XGBoost model;
# reserved concurrency 1 so at most one copy runs -- processing stays ordered
# and cost is bounded. Environment variables tell the handler its run id,
# DynamoDB table, audit bucket and the SHA-256 of the frozen model manifest
# (the handler refuses to score with a model whose manifest does not match).
# OMP_NUM_THREADS=1 stops XGBoost/NumPy from spawning extra threads.
resource "aws_lambda_function" "consumer" {
  count                          = local.function_count
  function_name                  = local.name
  role                           = aws_iam_role.consumer[0].arn
  package_type                   = "Image"
  image_uri                      = var.image_uri
  architectures                  = ["x86_64"]
  memory_size                    = 2048
  timeout                        = 60
  reserved_concurrent_executions = 1
  environment {
    variables = {
      RUN_ID          = var.run_id
      STATE_TABLE     = local.name
      AUDIT_BUCKET    = local.bucket
      MANIFEST_SHA256 = local.model_manifest
      OMP_NUM_THREADS = "1"
    }
  }
  # Create policy, log group and ECR pull permission before the function so the
  # first invocation can pull its image and write logs.
  depends_on = [aws_iam_role_policy.consumer, aws_cloudwatch_log_group.consumer, aws_ecr_repository_policy.lambda_retrieval]
}
# Connects the Kinesis stream to the Lambda (the "trigger"). Only created when
# both the stream and the failure bucket exist; only enabled when
# var.enable_consumer is true. Settings:
#   TRIM_HORIZON -- start from the oldest record so a replay is read fully;
#   batch_size 10, 1-second batching window, 1 concurrent batch per shard;
#   ReportBatchItemFailures -- the handler can report exactly which record
#     failed, so only that record (and later ones) are retried;
#   1 retry, records older than 1 hour dropped, no batch splitting;
#   on_failure -- details of records that still fail go to the S3 failure
#     bucket for later investigation instead of blocking the shard forever.
resource "aws_lambda_event_source_mapping" "replay" {
  count                              = local.failure_destination && var.provision_stream ? local.function_count : 0
  event_source_arn                   = aws_kinesis_stream.replay[0].arn
  function_name                      = aws_lambda_function.consumer[0].arn
  enabled                            = var.enable_consumer
  starting_position                  = "TRIM_HORIZON"
  batch_size                         = 10
  maximum_batching_window_in_seconds = 1
  parallelization_factor             = 1
  function_response_types            = ["ReportBatchItemFailures"]
  maximum_retry_attempts             = 1
  maximum_record_age_in_seconds      = 3600
  bisect_batch_on_function_error     = false
  destination_config {
    on_failure { destination_arn = var.provision_failure_bucket ? aws_s3_bucket.failures[0].arn : "arn:aws:s3:::${local.failure_bucket}" }
  }
  depends_on = [aws_iam_role_policy.consumer, aws_s3_bucket_public_access_block.failures, aws_s3_bucket_server_side_encryption_configuration.failures]
}
# Outputs: the ECR URL to push the image to, and the rendered runtime IAM
# policy (decoded to JSON) for review before deployment.
output "ecr_url" { value = try(aws_ecr_repository.runtime[0].repository_url, null) }
output "runtime_policy" { value = jsondecode(templatefile("${path.module}/runtime-policy.json.tftpl", { region = var.aws_region, account = var.aws_account_id, name = local.name, bucket = local.bucket, failure_bucket = local.failure_bucket, run_id = var.run_id })) }

# Isolated S3 bucket that receives the on-failure records from the event-source
# mapping. Kept separate from the data lake so failure dumps cannot mix with
# pipeline data. Private (public access blocked) and AES256 encrypted below.
resource "aws_s3_bucket" "failures" {
  count         = var.provision_failure_bucket ? 1 : 0
  bucket        = local.failure_bucket
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "failures" {
  count                   = var.provision_failure_bucket ? 1 : 0
  bucket                  = aws_s3_bucket.failures[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "failures" {
  count  = var.provision_failure_bucket ? 1 : 0
  bucket = aws_s3_bucket.failures[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Turns every JSON log line containing "stream_record_failed": 1 (printed by
# src/bda_pipeline/streaming.py when a record cannot be processed) into a +1
# on the custom metric IS459/Streaming/<run_id> RecordFailures.
resource "aws_cloudwatch_log_metric_filter" "record_failures" {
  count          = local.count
  name           = "${local.name}-record-failures"
  log_group_name = aws_cloudwatch_log_group.consumer[0].name
  pattern        = "{ $.stream_record_failed = 1 }"
  metric_transformation {
    name      = "RecordFailures"
    namespace = "IS459/Streaming/${var.run_id}"
    value     = "1"
  }
}
# Alarm when RecordFailures >= 1 within any 60-second period. Missing data
# (no failures logged) counts as OK. It notifies the shared failure SNS topic
# created by the foundation stack.
resource "aws_cloudwatch_metric_alarm" "record_failures" {
  count               = local.count
  alarm_name          = "${local.name}-record-failures"
  namespace           = "IS459/Streaming/${var.run_id}"
  metric_name         = "RecordFailures"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = ["arn:aws:sns:${var.aws_region}:${var.aws_account_id}:is459-g3-failures"]
}
# ECR repository policy: lets the Lambda service pull image layers from this
# repository, but only on behalf of this run's function (aws:sourceArn).
resource "aws_ecr_repository_policy" "lambda_retrieval" {
  count      = local.count
  repository = aws_ecr_repository.runtime[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "LambdaImageRetrieval"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
      # ECR's Lambda statement uses the repository attachment as its resource.
      Condition = {
        StringLike = { "aws:sourceArn" = "arn:aws:lambda:${var.aws_region}:${var.aws_account_id}:function:${local.name}" }
      }
    }]
  })
}

# -----------------------------------------------------------------------------
# Dashboard publisher: makes committed streaming scores visible
# -----------------------------------------------------------------------------
# DynamoDB results (consistent scan) + reviewed replay events (S3, pinned
# SHA-256) -> publisher Lambda (src/bda_pipeline/dashboard_streaming.py) ->
# s3://<dashboard bucket>/dashboard/streaming/<run_id>/snapshot.json ->
# CloudFront /streaming/<run_id>/snapshot.json -> Incoming AML view.
# Separate from the consumer, so a dashboard failure cannot change scores,
# sender state or audit. provision_publisher creates it with the schedule
# DISABLED; enable_publisher arms it until publisher_end_time.
resource "aws_cloudwatch_log_group" "publisher" {
  count             = local.publisher_count
  name              = "/aws/lambda/${local.name}-publisher"
  retention_in_days = 7
}
resource "aws_iam_role" "publisher" {
  count              = local.publisher_count
  name               = "${local.name}-publisher"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
# Read-only on scores, one input object, the audit listing (commit times) and
# the failure alarm; write only the run's snapshot key. Reviewed separately,
# like the consumer's runtime policy.
resource "aws_iam_role_policy" "publisher" {
  count  = local.publisher_count
  role   = aws_iam_role.publisher[0].id
  policy = templatefile("${path.module}/publisher-policy.json.tftpl", local.publisher_policy_inputs)
}
# Zip package (boto3 comes with the runtime). Reserved concurrency 1: one
# writer per run. Each invocation publishes every 15 s for ~50 s, because the
# scheduler's floor is one minute. CONSUMER_ENABLED follows enable_consumer, so
# the snapshot can tell a running replay from a paused one.
resource "aws_lambda_function" "publisher" {
  count                          = local.publisher_count
  function_name                  = "${local.name}-publisher"
  role                           = aws_iam_role.publisher[0].arn
  runtime                        = "python3.12"
  handler                        = "dashboard_streaming.handler"
  filename                       = var.lambda_zip_path
  source_code_hash               = filebase64sha256(var.lambda_zip_path)
  memory_size                    = 256
  timeout                        = 60
  reserved_concurrent_executions = 1
  environment {
    variables = {
      RUN_ID                   = var.run_id
      STATE_TABLE              = local.name
      INPUT_BUCKET             = local.bucket
      INPUT_KEY                = "streaming/${var.run_id}/input/events.jsonl"
      INPUT_SHA256             = var.replay_events_sha256
      SITE_BUCKET              = local.site_bucket
      MANIFEST_SHA256          = local.model_manifest
      CONSUMER_ENABLED         = tostring(var.enable_consumer)
      ALARM_NAME               = "${local.name}-record-failures"
      PUBLISH_INTERVAL_SECONDS = "15"
    }
  }
  depends_on = [aws_iam_role_policy.publisher, aws_cloudwatch_log_group.publisher]
}
# The scheduler invokes asynchronously. If the previous minute is still
# running, drop the event instead of queueing a backlog of publications.
resource "aws_lambda_function_event_invoke_config" "publisher" {
  count                        = local.publisher_count
  function_name                = aws_lambda_function.publisher[0].function_name
  maximum_event_age_in_seconds = 60
  maximum_retry_attempts       = 0
}
# Scheduler role: may only invoke the publisher, only for this account's
# default schedule group (confused-deputy protection).
resource "aws_iam_role" "publisher_schedule" {
  count = local.publisher_count
  name  = "${local.name}-publisher-schedule"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = var.aws_account_id, "aws:SourceArn" = "arn:aws:scheduler:${var.aws_region}:${var.aws_account_id}:schedule-group/default" } }
    }]
  })
}
resource "aws_iam_role_policy" "publisher_schedule" {
  count  = local.publisher_count
  role   = aws_iam_role.publisher_schedule[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = [aws_lambda_function.publisher[0].arn] }] })
}
# Once a minute, DISABLED unless enable_publisher; end_date stops it at the
# end of the authorized demo window even if nobody disables it.
resource "aws_scheduler_schedule" "publisher" {
  count                        = local.publisher_count
  name                         = "${local.name}-publisher"
  state                        = var.enable_publisher ? "ENABLED" : "DISABLED"
  schedule_expression          = "rate(1 minute)"
  schedule_expression_timezone = "UTC"
  end_date                     = var.publisher_end_time == null ? null : "${var.publisher_end_time}Z"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.publisher[0].arn
    role_arn = aws_iam_role.publisher_schedule[0].arn
    input    = jsonencode({})
    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 0
    }
  }
  depends_on = [aws_iam_role_policy.publisher_schedule]
}
# Any publisher error (invalid state, over-cap scan, AWS failure) keeps the
# last good snapshot; this alarm tells the team through the shared topic.
resource "aws_cloudwatch_metric_alarm" "publisher_errors" {
  count               = local.publisher_count
  alarm_name          = "${local.name}-publisher-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = "${local.name}-publisher" }
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = ["arn:aws:sns:${var.aws_region}:${var.aws_account_id}:is459-g3-failures"]
}
# Public configuration for the dashboard (publish as dashboard/streaming-config.json
# after review) and the rendered publisher policy for review.
output "publisher_configuration" {
  description = "Public snapshot location only. Publish as dashboard/streaming-config.json after review."
  value       = var.provision_publisher ? { schema_version = 1, run_id = var.run_id, snapshot_url = "/streaming/${var.run_id}/snapshot.json" } : null
}
output "publisher_policy" { value = jsondecode(templatefile("${path.module}/publisher-policy.json.tftpl", local.publisher_policy_inputs)) }
