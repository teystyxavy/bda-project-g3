variable "artifact_directory" { type = string }
variable "model_file" { type = string }
variable "pipeline_config" {
  type = object({ schema_version = number, artifact_prefix = string, model_prefix = string,
    model_manifest_sha256 = string, image_uri = string, processing_role_arn = string,
  distribution_id = string, dashboard_url = string, stage_versions = optional(map(string), {}) })
  validation {
    condition     = can(regex("^588738581042\\.dkr\\.ecr\\.ap-southeast-1\\.amazonaws\\.com/is459-g3-training@sha256:[0-9a-f]{64}$", var.pipeline_config.image_uri)) && length(jsonencode(var.pipeline_config)) < 3500
    error_message = "Use a digest-pinned Group 3 image and a bounded configuration."
  }
}
locals {
  account       = "588738581042"
  region        = "ap-southeast-1"
  bucket        = "is459-g3-${local.account}-${local.region}"
  s3            = "arn:aws:s3:::${local.bucket}"
  site          = "arn:aws:s3:::is459-g3-dashboard-${local.account}-${local.region}"
  arn           = "arn:aws"
  machine       = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-ingest"
  jobs          = [for name in ["curate", "score"] : "arn:aws:glue:${local.region}:${local.account}:job/is459-g3-ingest-${name}"]
  processing    = [for stage in ["baseline", "export"] : "arn:aws:sagemaker:${local.region}:${local.account}:processing-job/is459-g3-${stage}-*"]
  training      = "arn:aws:sagemaker:${local.region}:${local.account}:training-job/is459-g3-train-*"
  catalog       = ["arn:aws:glue:${local.region}:${local.account}:catalog", "arn:aws:glue:${local.region}:${local.account}:database/is459_g3_ingest", "arn:aws:glue:${local.region}:${local.account}:table/is459_g3_ingest/run_*"]
  files         = toset(concat(["control.zip", "bda_pipeline.zip", "runtime.gluewheels.zip", "curate_transactions.py", "score_transactions.py"], [for name in ["comparison.json", "selection.json", "features.json", "model-manifest.json", "deployment.json", var.model_file] : "model/${name}"]))
  ecr           = "arn:aws:ecr:${local.region}:${local.account}:repository/is459-g3-training"
  failure_topic = "arn:aws:sns:${local.region}:${local.account}:is459-g3-failures"
}
removed {
  from = aws_s3_object.artifact
  lifecycle { destroy = false }
}
# Retain the predecessor's immutable deployment prefix for provenance/recovery.
resource "aws_s3_object" "deployment_artifact" {
  for_each               = local.files
  bucket                 = local.bucket
  key                    = "${var.pipeline_config.artifact_prefix}/${each.value}"
  source                 = "${var.artifact_directory}/${each.value}"
  source_hash            = filesha256("${var.artifact_directory}/${each.value}")
  server_side_encryption = "AES256"
}
resource "aws_s3_object" "configuration" {
  bucket                 = local.bucket
  key                    = "pipeline/deployment.json"
  content                = jsonencode(var.pipeline_config)
  content_type           = "application/json"
  server_side_encryption = "AES256"
}
resource "aws_glue_catalog_database" "runs" { name = "is459_g3_ingest" }
resource "aws_iam_role" "glue" {
  name               = "is459-g3-ingest-glue"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "glue.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnLike = { "aws:SourceArn" = local.jobs } } }] })
}
resource "aws_iam_role_policy" "glue" {
  role = aws_iam_role.glue.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = local.s3 },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.s3, Condition = { StringLike = { "s3:prefix" = ["pipeline/*", "tmp/ingest/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.s3}/pipeline/*", "${local.s3}/tmp/ingest/*"] },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"], Resource = ["${local.s3}/pipeline/runs/*/curated/*", "${local.s3}/pipeline/runs/*/scored/*", "${local.s3}/tmp/ingest/*"] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = ["${local.s3}/pipeline/runs/*/curated_$folder$", "${local.s3}/pipeline/runs/*/scored_$folder$", "${local.s3}/tmp/ingest/*/curate_$folder$", "${local.s3}/tmp/ingest/*/score_$folder$"] },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"], Resource = "arn:aws:logs:${local.region}:${local.account}:log-group:/aws-glue/is459-g3-ingest*:*" },
    { Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*", Condition = { StringEquals = { "cloudwatch:namespace" = "Glue" } } }
  ] })
}
resource "aws_cloudwatch_log_group" "glue" {
  for_each          = toset(["curate/error", "curate/output", "score/error", "score/output"])
  name              = "/aws-glue/is459-g3-ingest-${each.key}"
  retention_in_days = 14
}
resource "aws_glue_job" "job" {
  for_each          = toset(["curate", "score"])
  name              = "is459-g3-ingest-${each.key}"
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
    script_location = "s3://${local.bucket}/${var.pipeline_config.artifact_prefix}/${each.key == "curate" ? "curate" : "score"}_transactions.py"
  }
  non_overridable_arguments = merge({
    "--job-bookmark-option"    = "job-bookmark-disable"
    "--custom-logGroup-prefix" = "/aws-glue/is459-g3-ingest-${each.key}"
    "--conf"                   = "spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs=true"
    }, each.key == "score" ? {
    "--MODEL_DIR"                       = "."
    "--MANIFEST_SHA256"                 = var.pipeline_config.model_manifest_sha256
    "--extra-py-files"                  = "s3://${local.bucket}/${var.pipeline_config.artifact_prefix}/bda_pipeline.zip"
    "--extra-files"                     = join(",", [for name in ["comparison.json", "selection.json", "features.json", "model-manifest.json", var.model_file] : "s3://${local.bucket}/${var.pipeline_config.model_prefix}/${name}"])
    "--additional-python-modules"       = "s3://${local.bucket}/${var.pipeline_config.artifact_prefix}/runtime.gluewheels.zip"
    "--python-modules-installer-option" = "--no-index"
  } : { "--behaviour" = "true" })
  depends_on = [aws_iam_role_policy.glue, aws_s3_object.deployment_artifact, aws_cloudwatch_log_group.glue]
}
resource "aws_iam_role" "processing" {
  name               = "is459-g3-ingest-processing"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "sagemaker.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnLike = { "aws:SourceArn" = local.processing } } }] })
}
resource "aws_iam_role_policy" "processing" {
  role = aws_iam_role.processing.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = local.s3 },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.s3, Condition = { StringLike = { "s3:prefix" = ["pipeline/*", "athena-results/*", "sagemaker/outputs/is459-g3-train-*/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.s3}/pipeline/*", "${local.s3}/athena-results/*", "${local.s3}/sagemaker/outputs/is459-g3-train-*/*"] },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = ["${local.s3}/pipeline/runs/*/baseline/*", "${local.s3}/pipeline/runs/*/export/*"] },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = local.ecr },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:DescribeLogStreams", "logs:PutLogEvents"], Resource = "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/sagemaker/ProcessingJobs:*" },
    { Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*", Condition = { StringEquals = { "cloudwatch:namespace" = ["AWS/SageMaker", "/aws/sagemaker/ProcessingJobs"] } } }
  ] })
}
resource "aws_iam_role" "control" {
  name               = "is459-g3-ingest-control"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "control" {
  role = aws_iam_role.control.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.site, Condition = { StringEquals = { "s3:prefix" = "dashboard/current.json" } } },
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = local.s3 },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.s3, Condition = { StringLike = { "s3:prefix" = ["athena-results/*", "pipeline/runs/*/curated/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.s3}/athena-results/*", "${local.s3}/pipeline/runs/*/curated/*"] },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = "${local.s3}/athena-results/*" },
    { Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetPartitions"], Resource = local.catalog },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.s3, Condition = { StringLike = { "s3:prefix" = "pipeline/*" } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.s3}/pipeline/*", "${local.site}/dashboard/current.json"] },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${local.s3}/pipeline/lock.json", "${local.s3}/pipeline/cache/*", "${local.s3}/pipeline/stage-cache/*", "${local.s3}/pipeline/runs/*/curated/*", "${local.s3}/pipeline/runs/*/scored/*", "${local.s3}/pipeline/runs/*/evidence/*", "${local.s3}/sagemaker/inputs/is459-g3-train-*/*", "${local.site}/dashboard/releases/*/*", "${local.site}/dashboard/current.json", "${local.site}/dashboard/index.html", "${local.site}/dashboard/dashboard.js", "${local.site}/dashboard/dashboard.css"] },
    { Effect = "Allow", Action = ["s3:DeleteObject"], Resource = "${local.s3}/pipeline/lock.json" },
    { Effect = "Allow", Action = ["glue:CreateTable", "glue:GetTable"], Resource = local.catalog },
    { Effect = "Allow", Action = ["glue:GetJobRuns", "glue:BatchStopJobRun"], Resource = local.jobs },
    { Effect = "Allow", Action = ["sagemaker:StopProcessingJob", "sagemaker:StopTrainingJob"], Resource = concat(local.processing, [local.training]) },
    { Effect = "Allow", Action = ["sagemaker:DescribeProcessingJob", "sagemaker:DescribeTrainingJob"], Resource = concat(local.processing, [local.training]) },
    { Effect = "Allow", Action = ["sagemaker:ListProcessingJobs", "sagemaker:ListTrainingJobs"], Resource = "*" },
    { Effect = "Allow", Action = ["athena:StartQueryExecution", "athena:GetQueryExecution"], Resource = "arn:aws:athena:${local.region}:${local.account}:workgroup/is459-g3-analysis" },
    { Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${local.region}:${local.account}:execution:is459-g3-ingest:*" },
    { Effect = "Allow", Action = ["cloudfront:CreateInvalidation"], Resource = "arn:aws:cloudfront::${local.account}:distribution/${var.pipeline_config.distribution_id}" },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.control.arn}:*" }
  ] })
}
resource "aws_cloudwatch_log_group" "control" {
  name              = "/aws/lambda/is459-g3-ingest-control"
  retention_in_days = 14
}
resource "aws_lambda_function" "control" {
  function_name                  = "is459-g3-ingest-control"
  role                           = aws_iam_role.control.arn
  runtime                        = "python3.12"
  handler                        = "bda_pipeline.ingest_control.lambda_handler"
  s3_bucket                      = local.bucket
  s3_key                         = aws_s3_object.deployment_artifact["control.zip"].key
  source_code_hash               = filebase64sha256("${var.artifact_directory}/control.zip")
  timeout                        = 120
  memory_size                    = 256
  reserved_concurrent_executions = 1
  environment { variables = { PIPELINE_CONFIG = jsonencode(var.pipeline_config) } }
  depends_on = [aws_iam_role_policy.control, aws_cloudwatch_log_group.control]
}
resource "aws_iam_role" "workflow" {
  name               = "is459-g3-ingest-workflow"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnEquals = { "aws:SourceArn" = local.machine } } }] })
}
resource "aws_iam_role_policy" "workflow" {
  role = aws_iam_role.workflow.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.control.arn },
    { Effect = "Allow", Action = ["glue:StartJobRun", "glue:GetJobRun", "glue:GetJobRuns", "glue:BatchStopJobRun"], Resource = local.jobs },
    { Effect = "Allow", Action = ["sagemaker:CreateProcessingJob", "sagemaker:DescribeProcessingJob", "sagemaker:StopProcessingJob", "sagemaker:CreateTrainingJob", "sagemaker:DescribeTrainingJob", "sagemaker:StopTrainingJob", "sagemaker:AddTags", "sagemaker:ListTags"], Resource = concat(local.processing, [local.training]) },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.processing.arn, "arn:aws:iam::${local.account}:role/is459-g3-sagemaker-training"], Condition = { StringEquals = { "iam:PassedToService" = "sagemaker.amazonaws.com" } } },
    { Effect = "Allow", Action = ["events:PutRule", "events:PutTargets", "events:DescribeRule"], Resource = [for name in ["StepFunctionsGetEventsForSageMakerProcessingJobsRule", "StepFunctionsGetEventsForSageMakerTrainingJobsRule"] : "arn:aws:events:${local.region}:${local.account}:rule/${name}"] },
    { Effect = "Allow", Action = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:StopQueryExecution", "athena:GetQueryResults"], Resource = "arn:aws:athena:${local.region}:${local.account}:workgroup/is459-g3-analysis" },
    { Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions"], Resource = local.catalog },
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = local.s3 },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.s3, Condition = { StringLike = { "s3:prefix" = ["pipeline/runs/*/curated/*", "athena-results/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.s3}/pipeline/runs/*/curated/*", "${local.s3}/athena-results/*"] },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = "${local.s3}/athena-results/*" },
    { Effect = "Allow", Action = ["sns:Publish"], Resource = local.failure_topic }
  ] })
}
resource "aws_sfn_state_machine" "ingest" {
  name       = "is459-g3-ingest"
  role_arn   = aws_iam_role.workflow.arn
  type       = "STANDARD"
  definition = templatefile("${path.module}/workflow.asl.json", { control_arn = aws_lambda_function.control.arn, failure_topic = local.failure_topic })
  depends_on = [aws_iam_role_policy.workflow, aws_glue_job.job, aws_iam_role_policy.processing, aws_s3_object.configuration]
}
resource "aws_cloudwatch_event_rule" "cancel_workers" {
  name = "is459-g3-ingest-terminal-cleanup"
  event_pattern = jsonencode({ source = ["aws.states"], "detail-type" = ["Step Functions Execution Status Change"],
  detail = { stateMachineArn = [local.machine], status = ["FAILED", "TIMED_OUT", "ABORTED"] } })
}
resource "aws_lambda_permission" "cancel_workers" {
  statement_id   = "IngestTerminalCleanup"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.control.function_name
  principal      = "events.amazonaws.com"
  source_arn     = aws_cloudwatch_event_rule.cancel_workers.arn
  source_account = local.account
}
resource "aws_cloudwatch_event_target" "cancel_workers" {
  rule = aws_cloudwatch_event_rule.cancel_workers.name
  arn  = aws_lambda_function.control.arn
  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 3
  }
  depends_on = [aws_lambda_permission.cancel_workers]
}
output "state_machine_arn" { value = aws_sfn_state_machine.ingest.arn }
output "deployment_sha256" { value = sha256(jsonencode(var.pipeline_config)) }
output "launcher_policy_json" {
  value = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.s3, Condition = { StringLike = { "s3:prefix" = ["pipeline/deployment.json", "pipeline/cache/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.s3}/pipeline/deployment.json", "${local.s3}/pipeline/cache/*"] },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = "${local.s3}/pipeline/runs/*/raw/*" },
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.machine },
    { Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${local.region}:${local.account}:execution:is459-g3-ingest:*" },
    { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = aws_lambda_function.control.arn }
  ] })
}
