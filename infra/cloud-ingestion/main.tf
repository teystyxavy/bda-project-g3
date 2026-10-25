# =============================================================================
# infra/cloud-ingestion/main.tf -- fully in-cloud data acquisition
# =============================================================================
# What it is: lets a pipeline run start with NO data on anyone's laptop.
# A Step Functions workflow (is459-g3-cloud-ingest, definition in
# workflow.asl.json) invokes a container Lambda (is459-g3-acquire, code in
# src/bda_pipeline/ingestion/cloud_acquisition.py, image from cloud-ingestion/Dockerfile)
# that downloads the IBM AML "HI-Small" transactions/accounts files from
# Kaggle, prepares the FX rates and inputs, and uploads them to
# s3://<bucket>/pipeline/runs/<run_id>/raw/. The workflow then starts the main
# ingestion workflow is459-g3-ingest (infra/ingestion) and waits for it.
# Downloads are cached under pipeline/acquisition/ (verified by SHA-256) so a
# later run can reuse them instead of calling Kaggle again.
#
# Deployed with scripts/aws/deploy-cloud-ingestion.ps1.
#
# -----------------------------------------------------------------------------
# variable "image_uri": the acquisition container image, which must be pinned
# by sha256 digest in this project's is459-g3-acquisition ECR repository.

variable "image_uri" {
  type = string
  validation {
    condition     = can(regex("^588738581042\\.dkr\\.ecr\\.ap-southeast-1\\.amazonaws\\.com/is459-g3-acquisition@sha256:[0-9a-f]{64}$", var.image_uri))
    error_message = "Pin the acquisition image digest in the project acquisition ECR repository."
  }
}
# variable "kaggle_secret_arn": optional Secrets Manager secret holding Kaggle
# API credentials. Empty string = download anonymously (public dataset); when
# set, the Lambda gets permission to read exactly that one secret.
variable "kaggle_secret_arn" {
  type    = string
  default = ""
  validation {
    condition     = var.kaggle_secret_arn == "" || can(regex("^arn:aws:secretsmanager:ap-southeast-1:588738581042:secret:[A-Za-z0-9/_+=.@-]+$", var.kaggle_secret_arn))
    error_message = "Use an existing project-account Secrets Manager ARN, or public unauthenticated access."
  }
}
# Fixed names/ARNs used by the IAM policies below (s3 = bucket ARN, machine =
# this workflow, batch = the ingestion workflow it starts, function = the
# acquisition Lambda).
locals {
  account       = "588738581042"
  region        = "ap-southeast-1"
  bucket        = "is459-g3-${local.account}-${local.region}"
  s3            = "arn:aws:s3:::${local.bucket}"
  machine       = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-cloud-ingest"
  batch         = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-ingest"
  function      = "arn:aws:lambda:${local.region}:${local.account}:function:is459-g3-acquire"
  failure_topic = "arn:aws:sns:${local.region}:${local.account}:is459-g3-failures"
}
# Private ECR repository for the acquisition image (immutable tags, scan on
# push, AES256 encrypted).
resource "aws_ecr_repository" "acquisition" {
  name                 = "is459-g3-acquisition"
  image_tag_mutability = "IMMUTABLE"
  encryption_configuration { encryption_type = "AES256" }
  image_scanning_configuration { scan_on_push = true }
}
# Repository policy: the Lambda service may pull the image, but only for the
# is459-g3-acquire function.
resource "aws_ecr_repository_policy" "lambda" {
  repository = aws_ecr_repository.acquisition.name
  policy     = jsonencode({ Version = "2012-10-17", Statement = [{ Sid = "AcquisitionLambdaPull", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Condition = { ArnEquals = { "aws:SourceArn" = local.function } } }] })
}
# Lambda log group with 14-day retention.
resource "aws_cloudwatch_log_group" "acquisition" {
  name              = "/aws/lambda/is459-g3-acquire"
  retention_in_days = 14
}
# Execution role of the acquisition Lambda (only Lambda may assume it).
resource "aws_iam_role" "acquisition" {
  name               = "is459-g3-acquire"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
# Acquisition Lambda permissions:
resource "aws_iam_role_policy" "acquisition" {
  role = aws_iam_role.acquisition.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    # - write its own log streams;
    # - find the bucket region;
    # - list only the deployment config, run cache and acquisition cache keys;
    # - read pipeline/deployment.json (model/artefact config written by
    #   infra/ingestion), cached run results and cached Kaggle downloads;
    # - write the download cache, the run's raw/ inputs, and an
    #   acquisition-failure.json evidence file if acquisition fails.
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.acquisition.arn}:*" },
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = local.s3 },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.s3, Condition = { StringLike = { "s3:prefix" = ["pipeline/deployment.json", "pipeline/cache/*", "pipeline/acquisition/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.s3}/pipeline/deployment.json", "${local.s3}/pipeline/cache/*", "${local.s3}/pipeline/acquisition/*"] },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = ["${local.s3}/pipeline/acquisition/*", "${local.s3}/pipeline/runs/*/raw/*", "${local.s3}/pipeline/runs/*/evidence/acquisition-failure.json"] }
  # ...plus, only when a Kaggle secret is configured, read that one secret.
  ], var.kaggle_secret_arn == "" ? [] : [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.kaggle_secret_arn }]) })
}
# The acquisition Lambda. Container image; 3 GB memory and 4 GB of /tmp disk
# because the HI-Small CSVs are large and are downloaded/unpacked locally;
# 900 s (the Lambda maximum) timeout for the download; reserved concurrency 1
# so two acquisitions can never run at once. AWS_SDK_UA_APP_ID only tags API
# calls with an application id in the user-agent string.
resource "aws_lambda_function" "acquisition" {
  function_name                  = "is459-g3-acquire"
  role                           = aws_iam_role.acquisition.arn
  package_type                   = "Image"
  image_uri                      = var.image_uri
  architectures                  = ["x86_64"]
  memory_size                    = 3072
  timeout                        = 900
  reserved_concurrent_executions = 1
  ephemeral_storage { size = 4096 }
  environment { variables = { KAGGLE_SECRET_ARN = var.kaggle_secret_arn, AWS_SDK_UA_APP_ID = "AWSSkill-SageMaker" } }
  depends_on = [aws_iam_role_policy.acquisition, aws_ecr_repository_policy.lambda]
}
# Role for the cloud-ingest state machine, trusted only for that machine.
resource "aws_iam_role" "workflow" {
  name               = "is459-g3-cloud-ingest"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnEquals = { "aws:SourceArn" = local.machine } } }] })
}
# Workflow permissions:
# - invoke the acquisition Lambda;
# - start the main ingestion workflow, then poll/stop its executions
#   (DescribeExecution/StopExecution) while waiting for it to finish;
# - manage the managed EventBridge rule Step Functions uses to learn when a
#   nested ("startExecution.sync") execution completes;
# - publish failures to the shared SNS topic.
resource "aws_iam_role_policy" "workflow" {
  role = aws_iam_role.workflow.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = local.function },
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.batch },
    { Effect = "Allow", Action = ["states:DescribeExecution", "states:StopExecution"], Resource = "arn:aws:states:${local.region}:${local.account}:execution:is459-g3-ingest:*" },
    { Effect = "Allow", Action = ["events:PutRule", "events:PutTargets", "events:DescribeRule"], Resource = "arn:aws:events:${local.region}:${local.account}:rule/StepFunctionsGetEventsForStepFunctionsExecutionRule" },
    { Effect = "Allow", Action = ["sns:Publish"], Resource = local.failure_topic }
  ] })
}
# The cloud-ingest state machine. templatefile() injects the Lambda, batch and
# topic ARNs into workflow.asl.json. STANDARD type for long runs.
resource "aws_sfn_state_machine" "cloud_ingest" {
  name       = "is459-g3-cloud-ingest"
  role_arn   = aws_iam_role.workflow.arn
  type       = "STANDARD"
  definition = templatefile("${path.module}/workflow.asl.json", { acquisition_arn = local.function, batch_arn = local.batch, failure_topic = local.failure_topic })
  depends_on = [aws_iam_role_policy.workflow, aws_lambda_function.acquisition]
}
# Outputs: the workflow ARN, and the minimal policy a human/CI identity needs to
# start and watch it (printed for review, not attached by Terraform).
output "state_machine_arn" { value = aws_sfn_state_machine.cloud_ingest.arn }
output "launcher_policy_json" {
  value = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.machine },
    { Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${local.region}:${local.account}:execution:is459-g3-cloud-ingest:*" }
  ] })
}
