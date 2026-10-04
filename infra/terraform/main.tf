locals {
  bucket_name = "${var.project_name}-${var.aws_account_id}-${var.aws_region}"
  log_prefix  = "/${var.project_name}/glue"
}

resource "aws_s3_bucket" "pipeline" {
  bucket        = local.bucket_name
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "pipeline" {
  bucket                  = aws_s3_bucket.pipeline.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "pipeline" {
  bucket = aws_s3_bucket.pipeline.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_versioning" "pipeline" {
  bucket = aws_s3_bucket.pipeline.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.pipeline.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.pipeline.arn, "${aws_s3_bucket.pipeline.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "temporary" {
  bucket     = aws_s3_bucket.pipeline.id
  depends_on = [aws_s3_bucket_versioning.pipeline]
  rule {
    id     = "temporary-files"
    status = "Enabled"
    filter { prefix = "tmp/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  rule {
    id     = "athena-results"
    status = "Enabled"
    filter { prefix = "athena-results/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 7 }
  }
}

resource "aws_s3_object" "glue_script" {
  bucket      = aws_s3_bucket.pipeline.id
  key         = "scripts/curate_transactions.py"
  source      = "${path.module}/../../jobs/curate_transactions.py"
  source_hash = filemd5("${path.module}/../../jobs/curate_transactions.py")
}

resource "aws_cloudwatch_log_group" "glue" {
  for_each          = toset(["error", "output"])
  name              = "${local.log_prefix}/${each.key}"
  retention_in_days = 14
}

resource "aws_iam_role" "glue" {
  name = "${var.project_name}-glue"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "glue.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy" "glue" {
  role = aws_iam_role.glue.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:ListBucket"], Resource = aws_s3_bucket.pipeline.arn
      },
      {
        Effect   = "Allow", Action = ["s3:GetObject"],
        Resource = [for prefix in ["raw", "scripts", "curated", "tmp"] : "${aws_s3_bucket.pipeline.arn}/${prefix}/*"]
      },
      {
        Effect   = "Allow", Action = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
        Resource = [for prefix in ["curated", "tmp"] : "${aws_s3_bucket.pipeline.arn}/${prefix}/*"]
      },
      {
        Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = [for group in aws_cloudwatch_log_group.glue : "${group.arn}:*"]
      }
    ]
  })
}

resource "aws_glue_job" "curate" {
  name              = "${var.project_name}-curate"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 10
  max_retries       = 0
  execution_property { max_concurrent_runs = 1 }
  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.pipeline.id}/${aws_s3_object.glue_script.key}"
  }
  default_arguments = {
    "--job-language"           = "python"
    "--job-bookmark-option"    = "job-bookmark-disable"
    "--custom-logGroup-prefix" = local.log_prefix
    "--TempDir"                = "s3://${aws_s3_bucket.pipeline.id}/tmp/"
    "--transactions"           = "s3://${aws_s3_bucket.pipeline.id}/raw/smoke/transactions.csv"
    "--accounts"               = "s3://${aws_s3_bucket.pipeline.id}/raw/smoke/accounts.csv"
    "--fx"                     = "s3://${aws_s3_bucket.pipeline.id}/raw/smoke/fx.csv"
    "--output"                 = "s3://${aws_s3_bucket.pipeline.id}/curated/smoke/transactions/"
    "--expected_rows"          = "1000"
  }
  depends_on = [aws_iam_role_policy.glue]
}

resource "aws_glue_catalog_database" "curated" {
  name = replace("${var.project_name}_curated", "-", "_")
}

resource "aws_athena_workgroup" "analysis" {
  name          = "${var.project_name}-analysis"
  force_destroy = false
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 1073741824
    result_configuration {
      output_location = "s3://${aws_s3_bucket.pipeline.id}/athena-results/"
      encryption_configuration { encryption_option = "SSE_S3" }
    }
  }
}

resource "aws_sns_topic" "failures" {
  name = "${var.project_name}-failures"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.failures.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_event_rule" "failures" {
  name = "${var.project_name}-glue-failures"
  event_pattern = jsonencode({
    source = ["aws.glue"], "detail-type" = ["Glue Job State Change"]
    detail = { jobName = [aws_glue_job.curate.name], state = ["FAILED", "TIMEOUT", "STOPPED"] }
  })
}

resource "aws_cloudwatch_event_target" "failures" {
  rule       = aws_cloudwatch_event_rule.failures.name
  arn        = aws_sns_topic.failures.arn
  role_arn   = aws_iam_role.failure_delivery.arn
  depends_on = [aws_iam_role_policy.failure_delivery]
}

resource "aws_iam_role" "failure_delivery" {
  name = "${var.project_name}-failure-delivery"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.failures.arn } }
    }]
  })
}

resource "aws_iam_role_policy" "failure_delivery" {
  role = aws_iam_role.failure_delivery.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sns:Publish", Resource = aws_sns_topic.failures.arn }]
  })
}

resource "aws_budgets_budget" "account" {
  name         = "${var.project_name}-account-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
