# =============================================================================
# infra/orchestration/main.tf -- reviewed full-batch Step Functions workflow
# =============================================================================
# What it is: a Step Functions state machine (is459-g3-reviewed-batch) that
# runs the reviewed end-to-end batch on the FULL dataset (5,078,345 rows):
#   * BQ1 branch: starts the frozen-model Glue scoring job (infra/scoring)
#     over the curated full_transactions data, writing scored/<run_id>/
#     transactions/ plus a _SUCCESS marker;
#   * BQ2 branch: runs the corridor SQL (sql/full-orchestrated-corridors.sql)
#     in the cost-capped Athena workgroup (the two are independent, and the
#     project architecture runs them side by side);
#   * on failure: publishes to the shared SNS failure topic.
# The state definition itself is workflow.asl.json.tftpl in this
# folder; this file fills in its parameters and grants the minimum IAM needed.
# An optional one-off EventBridge Scheduler schedule can start a single run.
#
# Gating: nothing is created unless provision_orchestration = true, and the
# execution input carries execution_approved so a paid run needs explicit
# approval. Prepared/validated by scripts/aws/prepare-orchestration.py and
# src/bda_pipeline/control/orchestration.py (which renders the same template).

locals {
  # count / schedule_count: on/off switches for the workflow and the scheduler.
  # name / machine_arn: the state machine name and its ARN, computed up front so
  #   the IAM trust policy can reference it before it exists.
  # bucket / *_prefix: S3 locations -- curated input, this run's scored output
  #   and Spark scratch space (all scoped by run_id).
  # job_arn, topic_arn, catalog/database/table_arn, workgroup_arn: the exact
  #   existing resources the workflow may touch (created by other stacks).
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
  # values = every parameter substituted into workflow.asl.json.tftpl:
  # run id, bucket, expected row count (used to reconcile scored output), the
  # input/output/temp URIs, the _SUCCESS marker key Spark writes on completion,
  # the frozen scoring script location, the model manifest SHA-256 (proves the
  # frozen model is used), the Glue job role, failure topic, Athena results
  # location, the partition-projection template and the corridor SQL text.
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
  # Render the state-machine JSON: each value is JSON-encoded and passed to the
  # template as <name>_json, so strings/numbers are inserted with correct quoting.
  definition = templatefile("${path.module}/workflow.asl.json.tftpl", { for key, value in local.values : "${key}_json" => jsonencode(value) })
  # Least-privilege IAM policy for the state machine role (also exposed as an
  # output for review). One statement per need:
  permissions = {
    Version = "2012-10-17"
    Statement = [
      # OnlyFrozenJob: start/monitor/stop runs of the is459-g3-score-frozen Glue job
      #   only (".sync" integration polls GetJobRun; BatchStopJobRun cancels it).
      # ReadVerifiedCatalog: Athena reads the full_transactions table definition.
      # OnlyBudgetedWorkgroup: run and read queries only in the 1 GiB-capped
      #   is459-g3-analysis workgroup.
      # ReadDefaultAthenaCatalog: Athena resolves the default AwsDataCatalog.
      # BucketLocation: needed by Athena before writing results.
      { Sid = "OnlyFrozenJob", Effect = "Allow", Action = ["glue:GetJob", "glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"], Resource = [local.job_arn] },
      { Sid = "ReadVerifiedCatalog", Effect = "Allow", Action = ["glue:GetTable", "glue:GetDatabase", "glue:GetPartitions"], Resource = [local.catalog_arn, local.database_arn, local.table_arn] },
      { Sid = "OnlyBudgetedWorkgroup", Effect = "Allow", Action = ["athena:GetWorkGroup", "athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:StopQueryExecution"], Resource = [local.workgroup_arn] },
      { Sid = "ReadDefaultAthenaCatalog", Effect = "Allow", Action = ["athena:GetDataCatalog"], Resource = ["arn:aws:athena:${var.aws_region}:${var.aws_account_id}:datacatalog/AwsDataCatalog"] },
      { Sid = "BucketLocation", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = [local.bucket_arn] },
      # ListOnlyReviewedPrefixes: list keys only under the input, output, temp and
      # athena-results prefixes (each prefix, "prefix/" and "prefix/*").
      {
        Sid       = "ListOnlyReviewedPrefixes", Effect = "Allow", Action = ["s3:ListBucket"], Resource = [local.bucket_arn]
        Condition = { StringLike = { "s3:prefix" = flatten([for prefix in [local.input_prefix, local.output_prefix, local.temp_prefix, "athena-results"] : [prefix, "${prefix}/", "${prefix}/*"]]) } }
      },
      # ReadCuratedAndQueryResults: Athena reads curated Parquet and its own result
      #   files; the workflow reads the scoring job's _SUCCESS marker to confirm
      #   completion (it cannot read the scored data itself).
      # WriteOnlyAthenaResults: Athena writes query results only to athena-results/.
      # NotifyExistingFailureTopic: publish failure alerts to the shared SNS topic.
      { Sid = "ReadCuratedAndQueryResults", Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.bucket_arn}/${local.input_prefix}/*", "${local.bucket_arn}/athena-results/*", "${local.bucket_arn}/${local.output_prefix}/_SUCCESS"] },
      { Sid = "WriteOnlyAthenaResults", Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], Resource = ["${local.bucket_arn}/athena-results/*"] },
      { Sid = "NotifyExistingFailureTopic", Effect = "Allow", Action = ["sns:Publish"], Resource = [local.topic_arn] }
    ]
  }
}

# Role assumed by Step Functions when running the state machine. Trust is
# limited to this account AND this exact state machine ARN.
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
# Attach the least-privilege permissions defined in local.permissions.
resource "aws_iam_role_policy" "workflow" {
  count  = local.count
  role   = aws_iam_role.workflow[0].id
  policy = jsonencode(local.permissions)
}
# The state machine itself. STANDARD type (not EXPRESS) because a batch run
# can last many minutes and needs exactly-once execution with full history.
# depends_on: the role's policy must exist before the machine can be created.
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
# Role used by EventBridge Scheduler to start the workflow; trusted only for
# this account and this schedule group.
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
# The scheduler's only permission: StartExecution on this state machine.
resource "aws_iam_role_policy" "scheduler" {
  count  = local.schedule_count
  role   = aws_iam_role.scheduler[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["states:StartExecution"], Resource = [local.machine_arn] }] })
}
# A ONE-OFF schedule "at(<time>)" (not a recurring cron) that starts one run of
# the state machine at var.scheduled_at UTC. If no time is given, it defaults
# to 2099 so it can never fire by accident. It is created DISABLED unless
# enable_schedule is true, and does nothing after completing.
resource "aws_scheduler_schedule" "batch" {
  count                        = local.schedule_count
  name                         = "is459-g3-${var.run_id}"
  group_name                   = aws_scheduler_schedule_group.batch[0].name
  state                        = var.enable_schedule ? "ENABLED" : "DISABLED"
  schedule_expression          = "at(${coalesce(var.scheduled_at, "2099-01-01T00:00:00")})"
  schedule_expression_timezone = "UTC"
  action_after_completion      = "NONE"
  flexible_time_window { mode = "OFF" }
  # Target = the Scheduler's universal SDK target calling sfn:StartExecution.
  # Name = run_id makes the execution name unique, so a duplicate start is
  # rejected rather than running (and paying) twice. The execution input passes
  # the run id, expected row count and the approval flag.
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:sfn:startExecution"
    role_arn = aws_iam_role.scheduler[0].arn
    input = jsonencode({
      StateMachineArn = local.machine_arn, Name = var.run_id,
      Input           = jsonencode({ run_id = var.run_id, expected_rows = 5078345, execution_approved = var.scheduled_execution_approved })
    })
    # Do not retry: if the start fails or is late by >60 s, skip it rather than
    # risk a surprise paid run later.
    retry_policy {
      maximum_event_age_in_seconds = 60
      maximum_retry_attempts       = 0
    }
  }
  depends_on = [aws_iam_role_policy.scheduler]
}
