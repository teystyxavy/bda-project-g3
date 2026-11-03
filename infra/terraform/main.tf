# =============================================================================
# infra/terraform/main.tf -- FOUNDATION stack (deploy this first)
# =============================================================================
# What it is: the shared base layer of the IS459 Group 3 AML pipeline. Every
# other stack under infra/ (ingestion, scoring, orchestration, streaming,
# sagemaker, hosting, triggers) refers to the resources created here by their
# fixed names rather than by Terraform references, so this stack must exist
# before any of them are applied.
#
# What it creates:
#   * The single pipeline S3 bucket  is459-g3-<account>-<region>  that holds
#     raw/ (Kaggle CSV + FX inputs), curated/ (Parquet written by Glue),
#     scripts/ (Glue job code), tmp/ (Spark scratch) and athena-results/.
#   * Bucket hardening: public-access block, AES256 encryption, versioning,
#     TLS-only bucket policy and lifecycle expiry of temporary prefixes.
#   * The original "smoke" Glue curate job (jobs/curate_transactions.py) with a
#     least-privilege IAM role and its CloudWatch log groups.
#   * The Glue Data Catalog database (is459_g3_curated) and the Athena
#     workgroup (is459-g3-analysis) used for the BQ2 corridor SQL.
#   * Failure alerting: an SNS topic (is459-g3-failures) with an e-mail
#     subscription, plus an EventBridge rule that forwards failed Glue runs.
#   * An AWS Budget with 50/80/100% e-mail alerts to cap student spend.
# Catalog tables and saved Athena queries live in catalog.tf (same stack).
#
# How it is run: scripts/aws/plan-aws.ps1 runs `terraform plan` in this
# directory; apply is a separate, human-approved step. State is kept locally
# (backend "local" in versions.tf).

locals {
  # locals = values computed once and reused in this file.
  # bucket_name: globally-unique S3 name built from project, account and region
  #   (e.g. is459-g3-588738581042-ap-southeast-1). Other stacks rebuild this same
  #   string themselves, so do not change the pattern.
  # log_prefix: CloudWatch log-group prefix that the Glue job writes to via the
  #   --custom-logGroup-prefix argument below.
  bucket_name = "${var.project_name}-${var.aws_account_id}-${var.aws_region}"
  log_prefix  = "/${var.project_name}/glue"
}

# The main data-lake bucket for the whole pipeline (raw -> curated -> results).
# force_destroy = false means `terraform destroy` refuses to delete it while it
# still contains objects, protecting the dataset and evidence from accidents.
resource "aws_s3_bucket" "pipeline" {
  bucket        = local.bucket_name
  force_destroy = false
}

# Blocks every form of public access (ACLs and bucket policies). The data is a
# financial-crime dataset, so nothing in this bucket should ever be public; the
# dashboard is served from a separate bucket in infra/hosting.
resource "aws_s3_bucket_public_access_block" "pipeline" {
  bucket                  = aws_s3_bucket.pipeline.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Default server-side encryption: every object is encrypted at rest with
# S3-managed keys (SSE-S3 / AES256). Free, and no KMS key to manage.
resource "aws_s3_bucket_server_side_encryption_configuration" "pipeline" {
  bucket = aws_s3_bucket.pipeline.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Versioning keeps old copies of overwritten/deleted objects so an accidental
# overwrite of raw or curated data can be rolled back.
resource "aws_s3_bucket_versioning" "pipeline" {
  bucket = aws_s3_bucket.pipeline.id
  versioning_configuration { status = "Enabled" }
}

# Bucket policy that DENIES any request not made over HTTPS
# (aws:SecureTransport = false), for every principal and every S3 action, on
# the bucket itself and on all objects. Enforces encryption in transit.
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

# Lifecycle rules keep storage costs down by expiring throw-away data.
# depends_on versioning: noncurrent-version rules only make sense once
# versioning is enabled, so Terraform must create versioning first.
resource "aws_s3_bucket_lifecycle_configuration" "temporary" {
  bucket     = aws_s3_bucket.pipeline.id
  depends_on = [aws_s3_bucket_versioning.pipeline]
  # Rule 1 -- tmp/ is Spark/Glue scratch space (--TempDir). Objects (and their
  # older versions) are deleted after 7 days; half-finished multipart uploads
  # from crashed jobs are cleaned up after 1 day.
  rule {
    id     = "temporary-files"
    status = "Enabled"
    filter { prefix = "tmp/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  # Rule 2 -- athena-results/ holds the CSV result file of every Athena query.
  # They are re-creatable, so they are also expired after 7 days.
  rule {
    id     = "athena-results"
    status = "Enabled"
    filter { prefix = "athena-results/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 7 }
  }
}

# Uploads the PySpark curate script (jobs/curate_transactions.py) to
# s3://<bucket>/scripts/ so Glue can load it. source_hash = MD5 of the local
# file: when the script changes, Terraform notices and re-uploads it.
resource "aws_s3_object" "glue_script" {
  bucket      = aws_s3_bucket.pipeline.id
  key         = "scripts/curate_transactions.py"
  source      = "${path.module}/../../jobs/curate_transactions.py"
  source_hash = filemd5("${path.module}/../../jobs/curate_transactions.py")
}

# Two log groups, /is459-g3/glue/error and /is459-g3/glue/output, receive the
# Glue job's stderr/stdout (Glue appends /error and /output to the custom
# prefix). Creating them here lets us set a 14-day retention instead of the
# default "never expire", and lets the IAM policy below name them exactly.
resource "aws_cloudwatch_log_group" "glue" {
  for_each          = toset(["error", "output"])
  name              = "${local.log_prefix}/${each.key}"
  retention_in_days = 14
}

# IAM role assumed by the Glue service when the curate job runs. The trust
# policy only lets glue.amazonaws.com assume it (sts:AssumeRole).
resource "aws_iam_role" "glue" {
  name = "${var.project_name}-glue"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "glue.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

# Inline permissions for the Glue curate role -- deliberately least privilege
# (no AWS-managed AWSGlueServiceRole policy is attached).
resource "aws_iam_role_policy" "glue" {
  role = aws_iam_role.glue.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Bucket-level calls Spark needs before reading/writing: find the bucket's
      # region and list keys (to discover input files and output partitions).
      {
        Effect = "Allow", Action = ["s3:GetBucketLocation", "s3:ListBucket"], Resource = aws_s3_bucket.pipeline.arn
      },
      # Read objects: raw/ (input CSVs), scripts/ (the job code itself), curated/
      # (Spark re-reads its own output when committing) and tmp/ (scratch files).
      {
        Effect   = "Allow", Action = ["s3:GetObject"],
        Resource = [for prefix in ["raw", "scripts", "curated", "tmp"] : "${aws_s3_bucket.pipeline.arn}/${prefix}/*"]
      },
      # Write objects only under curated/ (the Parquet output) and tmp/ (scratch).
      # DeleteObject is needed because Spark's file committer writes to a temporary
      # folder then moves/cleans up; AbortMultipartUpload cleans up failed large
      # uploads. raw/ stays read-only so the source data cannot be altered.
      {
        Effect   = "Allow", Action = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"],
        Resource = [for prefix in ["curated", "tmp"] : "${aws_s3_bucket.pipeline.arn}/${prefix}/*"]
      },
      # Write log lines into the two log groups created above (":*" = any stream).
      {
        Effect   = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"],
        Resource = [for group in aws_cloudwatch_log_group.glue : "${group.arn}:*"]
      }
    ]
  })
}

# The smoke-test Glue ETL job: reads the small 1,000-row sample in raw/smoke/,
# joins accounts and FX rates, converts amounts to USD and writes partitioned
# Parquet to curated/smoke/transactions/ (read by the smoke_transactions table
# in catalog.tf). Sized for cost control: Glue 5.0, 2 x G.1X workers (the
# minimum), 10-minute timeout, no automatic retries (a failing paid job is
# investigated, not re-run blindly) and only one run at a time.
resource "aws_glue_job" "curate" {
  name              = "${var.project_name}-curate"
  role_arn          = aws_iam_role.glue.arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 10
  max_retries       = 0
  execution_property { max_concurrent_runs = 1 }
  # glueetl = Spark ETL job type; script_location points at the uploaded file.
  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${aws_s3_bucket.pipeline.id}/${aws_s3_object.glue_script.key}"
  }
  # Arguments passed to the script (parsed with argparse in the job).
  # Bookmarks are disabled because each run re-processes a fixed input file;
  # --TempDir is Spark scratch; --expected_rows makes the job fail if the raw
  # CSV does not contain exactly the 1,000-row smoke sample (reconciliation check).
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
  # Make sure the IAM policy exists before the job can be started.
  depends_on = [aws_iam_role_policy.glue]
}

# Glue Data Catalog database that holds the table definitions Athena queries
# (smoke_transactions, full_transactions). Glue names may not contain hyphens,
# so "is459-g3_curated" becomes "is459_g3_curated".
resource "aws_glue_catalog_database" "curated" {
  name = replace("${var.project_name}_curated", "-", "_")
}

# Athena workgroup used for all project queries (BQ2 corridor analysis and
# reconciliation). enforce_workgroup_configuration stops clients overriding
# these settings; bytes_scanned_cutoff_per_query = 8 GiB cancels any query
# that would scan more, which caps the cost of a mistaken query (Athena bills
# per byte scanned). 8 GiB was sized for the curated dataset's full-scan
# corridor query (~2.6-3 GB at current size), with headroom for growth.
# Results go to athena-results/ encrypted with SSE-S3, and per-query metrics
# are published to CloudWatch.
resource "aws_athena_workgroup" "analysis" {
  name          = "${var.project_name}-analysis"
  force_destroy = false
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 8589934592
    result_configuration {
      output_location = "s3://${aws_s3_bucket.pipeline.id}/athena-results/"
      encryption_configuration { encryption_option = "SSE_S3" }
    }
  }
}

# SNS topic that every failure alert in the project is sent to. Other stacks
# (scoring, orchestration, ingestion, streaming) publish to it by its ARN
# arn:aws:sns:<region>:<account>:is459-g3-failures.
resource "aws_sns_topic" "failures" {
  name = "${var.project_name}-failures"
}

# E-mail subscription to the failure topic. AWS sends a confirmation e-mail;
# alerts only arrive after the recipient clicks the confirmation link.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.failures.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# EventBridge rule that matches "Glue Job State Change" events for the curate
# job when it ends in FAILED, TIMEOUT or STOPPED -- i.e. any unsuccessful end.
resource "aws_cloudwatch_event_rule" "failures" {
  name = "${var.project_name}-glue-failures"
  event_pattern = jsonencode({
    source = ["aws.glue"], "detail-type" = ["Glue Job State Change"]
    detail = { jobName = [aws_glue_job.curate.name], state = ["FAILED", "TIMEOUT", "STOPPED"] }
  })
}

# Sends matching events to the SNS topic, using the delivery role below.
resource "aws_cloudwatch_event_target" "failures" {
  rule       = aws_cloudwatch_event_rule.failures.name
  arn        = aws_sns_topic.failures.arn
  role_arn   = aws_iam_role.failure_delivery.arn
  depends_on = [aws_iam_role_policy.failure_delivery]
}

# Role that EventBridge assumes to publish to SNS. The ArnEquals condition on
# aws:SourceArn means only this specific rule can use the role (prevents the
# "confused deputy" problem where another rule borrows it).
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

# The delivery role's only permission: publish to the failure topic.
resource "aws_iam_role_policy" "failure_delivery" {
  role = aws_iam_role.failure_delivery.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sns:Publish", Resource = aws_sns_topic.failures.arn }]
  })
}

# Monthly cost budget for the whole account (limit from var.monthly_budget_usd,
# max USD 10). E-mails var.alert_email when ACTUAL spend passes 50%, 80% and
# 100% of the limit -- one notification block is generated per threshold.
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
