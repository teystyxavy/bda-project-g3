locals {
  count          = var.provision_orchestration ? 1 : 0
  schedule_count = var.provision_orchestration && var.provision_schedule ? 1 : 0
  name           = "is459-g3-reviewed-batch"
  machine_arn    = "arn:aws:states:${var.aws_region}:${var.aws_account_id}:stateMachine:${local.name}"
  bucket         = "is459-g3-${var.aws_account_id}-${var.aws_region}"
  bucket_arn     = "arn:aws:s3:::${local.bucket}"
  input_prefix   = "curated/full-optimized-20261005/transactions"
  output_prefix  = "scored/${var.run_id}/transactions"
  temp_prefix    = "tmp/scoring/${var.run_id}"
  job_arn        = "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:job/is459-g3-score-frozen"
  topic_arn      = "arn:aws:sns:${var.aws_region}:${var.aws_account_id}:is459-g3-failures"
  catalog_arn    = "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:catalog"
  database_arn   = "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:database/is459_g3_curated"
  table_arn      = "arn:aws:glue:${var.aws_region}:${var.aws_account_id}:table/is459_g3_curated/full_transactions"
  workgroup_arn  = "arn:aws:athena:${var.aws_region}:${var.aws_account_id}:workgroup/is459-g3-analysis"
  values = {
    run_id              = var.run_id, bucket = local.bucket, rows = 5078345,
    input_uri           = "s3://${local.bucket}/${local.input_prefix}/",
    output_prefix       = local.output_prefix, output_uri = "s3://${local.bucket}/${local.output_prefix}/",
    marker_key          = "${local.output_prefix}/_SUCCESS", temp_prefix = local.temp_prefix,
    temp_uri            = "s3://${local.bucket}/${local.temp_prefix}/",
    script_uri          = "s3://${local.bucket}/scoring-artifacts/frozen-localized-20261005/score_transactions.py",
    manifest_sha256     = "323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f",
    job_role            = "arn:aws:iam::${var.aws_account_id}:role/is459-g3-glue-frozen-scoring",
    notification_topic  = local.topic_arn, athena_results = "s3://${local.bucket}/athena-results/",
    projection_template = "s3://${local.bucket}/${local.input_prefix}/txn_date=$${txn_date}/",
    query_sql           = file("${path.module}/../../sql/full-orchestrated-corridors.sql")
  }
  definition = templatefile("${path.module}/workflow.asl.json.tftpl", { for key, value in local.values : "${key}_json" => jsonencode(value) })
  permissions = {
    Version = "2012-10-17"
    Statement = [
      { Sid = "OnlyFrozenJob", Effect = "Allow", Action = ["glue:GetJob", "glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"], Resource = [local.job_arn] },
      { Sid = "ReadVerifiedCatalog", Effect = "Allow", Action = ["glue:GetTable", "glue:GetDatabase", "glue:GetPartitions"], Resource = [local.catalog_arn, local.database_arn, local.table_arn] },
      { Sid = "OnlyBudgetedWorkgroup", Effect = "Allow", Action = ["athena:GetWorkGroup", "athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution"], Resource = [local.workgroup_arn] },
      { Sid = "ReadDefaultAthenaCatalog", Effect = "Allow", Action = ["athena:GetDataCatalog"], Resource = ["arn:aws:athena:${var.aws_region}:${var.aws_account_id}:datacatalog/AwsDataCatalog"] },
      { Sid = "BucketLocation", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = [local.bucket_arn] },
      {
        Sid       = "ListOnlyReviewedPrefixes", Effect = "Allow", Action = ["s3:ListBucket"], Resource = [local.bucket_arn]
        Condition = { StringLike = { "s3:prefix" = flatten([for prefix in [local.input_prefix, local.output_prefix, local.temp_prefix, "athena-results"] : [prefix, "${prefix}/", "${prefix}/*"]]) } }
      },
      { Sid = "ReadCuratedAndQueryResults", Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.bucket_arn}/${local.input_prefix}/*", "${local.bucket_arn}/athena-results/*", "${local.bucket_arn}/${local.output_prefix}/_SUCCESS"] },
      { Sid = "WriteOnlyAthenaResults", Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], Resource = ["${local.bucket_arn}/athena-results/*"] },
      { Sid = "NotifyExistingFailureTopic", Effect = "Allow", Action = ["sns:Publish"], Resource = [local.topic_arn] }
    ]
  }
}

resource "aws_iam_role" "workflow" {
  count = local.count
  name  = "is459-g3-reviewed-batch-workflow"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = var.aws_account_id }, ArnEquals = { "aws:SourceArn" = local.machine_arn } }
    }]
  })
}
resource "aws_iam_role_policy" "workflow" {
  count  = local.count
  role   = aws_iam_role.workflow[0].id
  policy = jsonencode(local.permissions)
}
resource "aws_sfn_state_machine" "batch" {
  count      = local.count
  name       = local.name
  type       = "STANDARD"
  role_arn   = aws_iam_role.workflow[0].arn
  definition = local.definition
  depends_on = [aws_iam_role_policy.workflow]
}

# Optional one-off Scheduler; absent by default and separately gated when enabled.
resource "aws_scheduler_schedule_group" "batch" {
  count = local.schedule_count
  name  = "is459-g3-reviewed-batch"
}
resource "aws_iam_role" "scheduler" {
  count = local.schedule_count
  name  = "is459-g3-reviewed-batch-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = var.aws_account_id, "aws:SourceArn" = aws_scheduler_schedule_group.batch[0].arn } }
    }]
  })
}
resource "aws_iam_role_policy" "scheduler" {
  count  = local.schedule_count
  role   = aws_iam_role.scheduler[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["states:StartExecution"], Resource = [local.machine_arn] }] })
}
resource "aws_scheduler_schedule" "batch" {
  count                        = local.schedule_count
  name                         = "is459-g3-${var.run_id}"
  group_name                   = aws_scheduler_schedule_group.batch[0].name
  state                        = var.enable_schedule ? "ENABLED" : "DISABLED"
  schedule_expression          = "at(${coalesce(var.scheduled_at, "2099-01-01T00:00:00")})"
  schedule_expression_timezone = "UTC"
  action_after_completion      = "NONE"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:sfn:startExecution"
    role_arn = aws_iam_role.scheduler[0].arn
    input = jsonencode({
      StateMachineArn = local.machine_arn, Name = var.run_id,
      Input           = jsonencode({ run_id = var.run_id, expected_rows = 5078345, execution_approved = var.scheduled_execution_approved })
    })
    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 0
    }
  }
  depends_on = [aws_iam_role_policy.scheduler]
}
