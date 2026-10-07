# =============================================================================
# infra/scoring/main.tf -- frozen-model Glue scoring job (BQ1 risk scores)
# =============================================================================
# What it is: a Glue PySpark job (is459-g3-score-frozen) that applies the
# FROZEN, already-reviewed XGBoost AML model to curated transactions and
# writes a risk score per transaction to s3://<bucket>/scored/<run_id>/
# transactions/. Those scores feed the BQ1 ranked compliance review queue.
# The script (jobs/score_transactions.py), helper package (bda_pipeline.zip),
# offline Python wheels and model files are uploaded beforehand to an
# immutable scoring-artifacts/<name>/ prefix by scripts/aws/
# prepare-cloud-scoring.py; the manifest SHA-256 proves which model is used.
#
# Gating: provision_scoring = false (default) creates nothing; applying never
# starts a run -- runs are started by the orchestration workflow
# (infra/orchestration) or manually after approval.

locals {
  # count: on/off switch. job_arn: used in the role trust condition.
  # output_prefix / temp_prefix: per-run output and Spark scratch locations.
  # log_prefix: custom CloudWatch log-group prefix for the job.
  # readable / writable: S3 prefixes the job may read (input, artefacts, its own
  # output and scratch) vs. write (only its own output and scratch).
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
  # artifact_files: model side-files shipped to the job's working directory via
  # --extra-files (model comparison, feature list, model selection, the UBJ
  # XGBoost model and the manifest listing their hashes).
  # logs: ARNs of the error/output log groups.
  artifact_files = ["comparison.json", "features.json", "selection.json", var.model_filename, "model-manifest.json"]
  logs           = [for suffix in ["error", "output"] : "arn:aws:logs:${var.aws_region}:${var.aws_account_id}:log-group:${local.log_prefix}/${suffix}:*"]
  # Complete runtime permissions. No broad AWS-managed policy is attached.
  permissions = {
    Version = "2012-10-17"
    Statement = [
      # BucketLocation: Spark/EMRFS looks up the bucket region first.
      { Sid = "BucketLocation", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = [local.bucket_arn] },
      # ListOnlyRunPrefixes: listing is limited to the readable prefixes plus the
      # two exact "_$folder$" marker keys.
      {
        Sid       = "ListOnlyRunPrefixes", Effect = "Allow", Action = ["s3:ListBucket"], Resource = [local.bucket_arn]
        Condition = { StringLike = { "s3:prefix" = concat(flatten([for prefix in local.readable : [prefix, "${prefix}/*"]]), local.folder_markers) } }
      },
      # ReadInputArtifactsAndOwnOutput: read curated input, model artefacts, and its
      # own output/scratch (Spark re-reads during commit).
      {
        Sid      = "ReadInputArtifactsAndOwnOutput", Effect = "Allow", Action = ["s3:GetObject"]
        Resource = [for prefix in local.readable : "${local.bucket_arn}/${prefix}/*"]
      },
      # WriteAndCommitOwnOutput: write/delete only inside this run's output and
      # scratch prefixes (delete + multipart actions are needed by Spark's commit).
      {
        Sid      = "WriteAndCommitOwnOutput", Effect = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource = [for prefix in local.writable : "${local.bucket_arn}/${prefix}/*"]
      },
      # WriteOnlyScoringLogs: log lines into the two scoring log groups.
      {
        Sid      = "WriteOnlyScoringLogs", Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = local.logs
      },
      # CommitExactRunRootMarkers: the two "<prefix>_$folder$" keys explained above.
      {
        Sid      = "CommitExactRunRootMarkers", Effect = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = [for marker in local.folder_markers : "${local.bucket_arn}/${marker}"]
      }
    ]
  }
}

# error/output log groups for the job, 14-day retention.
resource "aws_cloudwatch_log_group" "scoring" {
  for_each          = var.provision_scoring ? toset(["error", "output"]) : toset([])
  name              = "${local.log_prefix}/${each.key}"
  retention_in_days = 14
}
# Role the Glue job runs as, trusted only when Glue acts for this account and
# this exact job.
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
# Attach the complete least-privilege policy from local.permissions.
resource "aws_iam_role_policy" "scoring" {
  count  = local.count
  role   = aws_iam_role.scoring[0].id
  policy = jsonencode(local.permissions)
}
# The scoring job: Glue 5.0, 2 x G.1X workers, 10-minute timeout, no automatic
# retries, one run at a time (cost control).
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
  # non_overridable_arguments: a caller starting the job CANNOT change these, so
  # a run can only ever score the configured input with the frozen model:
  #   --INPUT / --OUTPUT        curated input and this run's output;
  #   --EXPECTED_ROWS           row count the job must reconcile against;
  #   --MANIFEST_SHA256         hash of the reviewed model manifest;
  #   --MODEL_DIR "."           model files are placed in the working directory;
  #   --extra-py-files          bda_pipeline package (feature + scoring code);
  #   --extra-files             model artefacts (see local.artifact_files);
  #   --additional-python-modules + --no-index: install pinned wheels from S3
  #                             only, never from the public internet (PyPI);
  #   bookmarks disabled        each run is a fresh full scoring pass.
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
# EventBridge rule: catches the scoring job ending FAILED/TIMEOUT/STOPPED.
resource "aws_cloudwatch_event_rule" "failure" {
  count = local.count
  name  = "is459-g3-frozen-scoring-failures"
  event_pattern = jsonencode({
    source = ["aws.glue"], "detail-type" = ["Glue Job State Change"]
    detail = { jobName = [local.job_name], state = ["FAILED", "TIMEOUT", "STOPPED"] }
  })
}
# Role EventBridge uses to deliver to SNS, trusted only for that rule.
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
# Its only permission: publish to the shared failure topic.
resource "aws_iam_role_policy" "failure" {
  count = local.count
  role  = aws_iam_role.failure[0].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sns:Publish", Resource = "arn:aws:sns:${var.aws_region}:${var.aws_account_id}:is459-g3-failures" }]
  })
}
# Deliver matching events to the shared is459-g3-failures SNS topic.
resource "aws_cloudwatch_event_target" "failure" {
  count      = local.count
  rule       = aws_cloudwatch_event_rule.failure[0].name
  arn        = "arn:aws:sns:${var.aws_region}:${var.aws_account_id}:is459-g3-failures"
  role_arn   = aws_iam_role.failure[0].arn
  depends_on = [aws_iam_role_policy.failure]
}
