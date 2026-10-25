# =============================================================================
# infra/streaming/main.tf -- OPTIONAL real-time streaming demo
# =============================================================================
# What it is: a small, separately-gated demonstration that the frozen AML
# model can score transactions one at a time as they arrive, instead of only
# in the nightly Glue batch. Flow:
#   replay producer (scripts/streaming/replay-streaming-demo.py)
#     -> Kinesis Data Stream (1 shard)
#     -> Lambda consumer (container image built from streaming/Dockerfile,
#        code in streaming/handler.py + src/bda_pipeline/scoring/streaming.py)
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
  #   stream_count   -- the Kinesis shard (the only always-billing component).
  # Names: every per-run resource is called is459-g3-<run_id>, so a fresh replay
  # run gets fresh, isolated resources. The *_arn values are pre-computed so the
  # IAM runtime policy can name exact resources even before they exist.
  count          = var.provision_streaming ? 1 : 0
  function_count = var.provision_streaming && var.image_uri != null ? 1 : 0
  # The shard is the only resource that bills while idle; provision_stream parks it.
  stream_count   = var.provision_streaming && var.provision_stream ? 1 : 0
  name           = "is459-g3-${var.run_id}"
  bucket         = "is459-g3-${var.aws_account_id}-${var.aws_region}"
  failure_bucket = "is459-g3-stream-failures-${var.aws_account_id}-${var.aws_region}"
  stream_arn     = "arn:aws:kinesis:${var.aws_region}:${var.aws_account_id}:stream/${local.name}"
  table_arn      = "arn:aws:dynamodb:${var.aws_region}:${var.aws_account_id}:table/${local.name}"
  log_arn        = "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:/aws/lambda/${local.name}"
}
# Private ECR repository holding the consumer's container image. IMMUTABLE tags
# mean a pushed tag can never be overwritten (the Lambda is pinned by digest
# anyway); images are scanned for known vulnerabilities on push and encrypted.
resource "aws_ecr_repository" "runtime" {
  count                = local.count
  name                 = "is459-g3-streaming"
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
      MANIFEST_SHA256 = "323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f"
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
  count                              = var.provision_failure_bucket && var.provision_stream ? local.function_count : 0
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
    on_failure { destination_arn = aws_s3_bucket.failures[0].arn }
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
# src/bda_pipeline/scoring/streaming.py when a record cannot be processed) into a +1
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
