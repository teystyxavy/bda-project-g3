locals {
  count         = var.provision_scoring ? 1 : 0
  bucket        = "is459-g3-${var.aws_account_id}-${var.aws_region}"
  bucket_arn    = "arn:aws:s3:::${local.bucket}"
  job_name      = "is459-g3-score-frozen"
  job_arn       = "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:job/${local.job_name}"
  output_prefix = "scored/${var.run_id}/transactions"
  temp_prefix   = "tmp/scoring/${var.run_id}"
  log_prefix    = "/is459-g3/scoring/glue"
  readable      = [var.input_prefix, var.artifact_prefix, local.output_prefix, local.temp_prefix]
  writable      = [local.output_prefix, local.temp_prefix]
  # EMRFS probes a sibling _$folder$ key when committing a directory.
  # Limit this exception to the two exact run roots, never prefix*.
  folder_markers = [for prefix in local.writable : "${prefix}_$folder$"]
  artifact_files = ["comparison.json", "features.json", "selection.json", var.model_filename, "model-manifest.json"]
  logs           = [for suffix in ["error", "output"] : "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:${local.log_prefix}/${suffix}:*"]
  # Complete runtime permissions. No broad AWS-managed policy is attached.
  permissions = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "BucketLocation", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = [local.bucket_arn] },
      {
        Sid       = "ListOnlyRunPrefixes", Effect = "Allow", Action = ["s3:ListBucket"], Resource = [local.bucket_arn]
        Condition = { StringLike = { "s3:prefix" = concat(flatten([for prefix in local.readable : [prefix, "${prefix}/*"]]), local.folder_markers) } }
      },
      {
        Sid      = "ReadInputArtifactsAndOwnOutput", Effect = "Allow", Action = ["s3:GetObject"]
        Resource = [for prefix in local.readable : "${local.bucket_arn}/${prefix}/*"]
      },
      {
        Sid      = "WriteAndCommitOwnOutput", Effect = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource = [for prefix in local.writable : "${local.bucket_arn}/${prefix}/*"]
      },
      {
        Sid      = "WriteOnlyScoringLogs", Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = local.logs
      },
      {
        Sid      = "CommitExactRunRootMarkers", Effect = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [for marker in local.folder_markers : "${local.bucket_arn}/${marker}"]
      }
    ]
  }
}

resource "aws_cloudwatch_log_group" "scoring" {
  for_each          = var.provision_scoring ? toset(["error", "output"]) : toset([])
  name              = "${local.log_prefix}/${each.key}"
  retention_in_days = 14
}
resource "aws_iam_role" "scoring" {
  count = local.count
  name  = "is459-g3-glue-frozen-scoring"
  # Strict conditions remain fail-closed. Service-populated context must be
  # verified in the approved smoke run; never relax automatically on a failure.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "glue.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.aws_account_id }
        ArnEquals    = { "aws:SourceArn" = local.job_arn }
      }
    }]
  })
}
resource "aws_iam_role_policy" "scoring" {
  count  = local.count
  role   = aws_iam_role.scoring[0].id
  policy = jsonencode(local.permissions)
}
resource "aws_glue_job" "scoring" {
  count             = local.count
  name              = local.job_name
  role_arn          = aws_iam_role.scoring[0].arn
  glue_version      = "5.0"
  worker_type       = "G.1X"
  number_of_workers = 2
  timeout           = 10
  max_retries       = 0
  execution_property { max_concurrent_runs = 1 }
  command {
    name            = "glueetl"
    python_version  = "3"
    script_location = "s3://${local.bucket}/${var.artifact_prefix}/score_transactions.py"
  }
  non_overridable_arguments = {
    "--INPUT"                           = "s3://${local.bucket}/${var.input_prefix}/"
    "--OUTPUT"                          = "s3://${local.bucket}/${local.output_prefix}/"
    "--EXPECTED_ROWS"                   = tostring(var.expected_rows)
    "--MANIFEST_SHA256"                 = var.manifest_sha256
    "--MODEL_DIR"                       = "."
    "--extra-py-files"                  = "s3://${local.bucket}/${var.artifact_prefix}/bda_pipeline.zip"
    "--extra-files"                     = join(",", [for name in local.artifact_files : "s3://${local.bucket}/${var.artifact_prefix}/${name}"])
    "--additional-python-modules"       = "s3://${local.bucket}/${var.artifact_prefix}/runtime.gluewheels.zip"
    "--python-modules-installer-option" = "--no-index"
    "--job-bookmark-option"             = "job-bookmark-disable"
    # Glue's marker-disabled final commit calls EMRFS mkdirs.
    # A success marker stays inside the output prefix and makes completion explicit.
    "--conf"                   = "spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs=true"
    "--custom-logGroup-prefix" = local.log_prefix
    "--TempDir"                = "s3://${local.bucket}/${local.temp_prefix}/"
  }
  depends_on = [aws_iam_role_policy.scoring, aws_cloudwatch_log_group.scoring]
}

# Observe failures only; this is not a schedule or a job-start trigger.
resource "aws_cloudwatch_event_rule" "failure" {
  count = local.count
  name  = "is459-g3-frozen-scoring-failures"
  event_pattern = jsonencode({
    source = ["aws.glue"], "detail-type" = ["Glue Job State Change"]
    detail = { jobName = [local.job_name], state = ["FAILED", "TIMEOUT", "STOPPED"] }
  })
}
resource "aws_iam_role" "failure" {
  count = local.count
  name  = "is459-g3-scoring-failure-delivery"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "events.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.aws_account_id }
        ArnEquals    = { "aws:SourceArn" = "arn:aws:events:${var.aws_region}:${var.aws_account_id}:rule/is459-g3-frozen-scoring-failures" }
      }
    }]
  })
}
resource "aws_iam_role_policy" "failure" {
  count = local.count
  role  = aws_iam_role.failure[0].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sns:Publish", Resource = "arn:aws:sns:${var.aws_region}:${var.aws_account_id}:is459-g3-failures" }]
  })
}
resource "aws_cloudwatch_event_target" "failure" {
  count      = local.count
  rule       = aws_cloudwatch_event_rule.failure[0].name
  arn        = "arn:aws:sns:${var.aws_region}:${var.aws_account_id}:is459-g3-failures"
  role_arn   = aws_iam_role.failure[0].arn
  depends_on = [aws_iam_role_policy.failure]
}
