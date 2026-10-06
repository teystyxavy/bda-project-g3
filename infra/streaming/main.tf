locals {
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
resource "aws_ecr_repository" "runtime" {
  count                = local.count
  name                 = "is459-g3-streaming"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  encryption_configuration { encryption_type = "AES256" }
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_kinesis_stream" "replay" {
  count            = local.stream_count
  name             = local.name
  shard_count      = 1
  retention_period = 24
  encryption_type  = "KMS"
  kms_key_id       = "alias/aws/kinesis"
  stream_mode_details { stream_mode = "PROVISIONED" }
}
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
resource "aws_cloudwatch_log_group" "consumer" {
  count             = local.count
  name              = "/aws/lambda/${local.name}"
  retention_in_days = 7
}
resource "aws_iam_role" "consumer" {
  count              = local.count
  name               = local.name
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
# Runtime policy is generated/reviewed separately from deployment credentials.
resource "aws_iam_role_policy" "consumer" {
  count  = local.count
  role   = aws_iam_role.consumer[0].id
  policy = templatefile("${path.module}/runtime-policy.json.tftpl", { region = var.aws_region, account = var.aws_account_id, name = local.name, bucket = local.bucket, failure_bucket = local.failure_bucket, run_id = var.run_id })
}
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
  depends_on = [aws_iam_role_policy.consumer, aws_cloudwatch_log_group.consumer, aws_ecr_repository_policy.lambda_retrieval]
}
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
output "ecr_url" { value = try(aws_ecr_repository.runtime[0].repository_url, null) }
output "runtime_policy" { value = jsondecode(templatefile("${path.module}/runtime-policy.json.tftpl", { region = var.aws_region, account = var.aws_account_id, name = local.name, bucket = local.bucket, failure_bucket = local.failure_bucket, run_id = var.run_id })) }

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
