variable "image_uri" {
  type = string
  validation {
    condition     = can(regex("^588738581042\\.dkr\\.ecr\\.ap-southeast-1\\.amazonaws\\.com/is459-g3-acquisition@sha256:[0-9a-f]{64}$", var.image_uri))
    error_message = "Pin the acquisition image digest in the project acquisition ECR repository."
  }
}
variable "kaggle_secret_arn" {
  type    = string
  default = ""
  validation {
    condition     = var.kaggle_secret_arn == "" || can(regex("^arn:aws:secretsmanager:ap-southeast-1:588738581042:secret:[A-Za-z0-9/_+=.@-]+$", var.kaggle_secret_arn))
    error_message = "Use an existing project-account Secrets Manager ARN, or public unauthenticated access."
  }
}
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
resource "aws_ecr_repository" "acquisition" {
  name                 = "is459-g3-acquisition"
  image_tag_mutability = "IMMUTABLE"
  encryption_configuration { encryption_type = "AES256" }
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_ecr_repository_policy" "lambda" {
  repository = aws_ecr_repository.acquisition.name
  policy     = jsonencode({ Version = "2012-10-17", Statement = [{ Sid = "AcquisitionLambdaPull", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Condition = { ArnEquals = { "aws:SourceArn" = local.function } } }] })
}
resource "aws_cloudwatch_log_group" "acquisition" {
  name              = "/aws/lambda/is459-g3-acquire"
  retention_in_days = 14
}
resource "aws_iam_role" "acquisition" {
  name               = "is459-g3-acquire"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "acquisition" {
  role = aws_iam_role.acquisition.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.acquisition.arn}:*" },
    { Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = local.s3 },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = local.s3, Condition = { StringLike = { "s3:prefix" = ["pipeline/deployment.json", "pipeline/cache/*", "pipeline/acquisition/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${local.s3}/pipeline/deployment.json", "${local.s3}/pipeline/cache/*", "${local.s3}/pipeline/acquisition/*"] },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = ["${local.s3}/pipeline/acquisition/*", "${local.s3}/pipeline/runs/*/raw/*", "${local.s3}/pipeline/runs/*/evidence/acquisition-failure.json"] }
  ], var.kaggle_secret_arn == "" ? [] : [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.kaggle_secret_arn }]) })
}
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
resource "aws_iam_role" "workflow" {
  name               = "is459-g3-cloud-ingest"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole", Condition = { StringEquals = { "aws:SourceAccount" = local.account }, ArnEquals = { "aws:SourceArn" = local.machine } } }] })
}
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
resource "aws_sfn_state_machine" "cloud_ingest" {
  name       = "is459-g3-cloud-ingest"
  role_arn   = aws_iam_role.workflow.arn
  type       = "STANDARD"
  definition = templatefile("${path.module}/workflow.asl.json", { acquisition_arn = local.function, batch_arn = local.batch, failure_topic = local.failure_topic })
  depends_on = [aws_iam_role_policy.workflow, aws_lambda_function.acquisition]
}
output "state_machine_arn" { value = aws_sfn_state_machine.cloud_ingest.arn }
output "launcher_policy_json" {
  value = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.machine },
    { Effect = "Allow", Action = ["states:DescribeExecution"], Resource = "arn:aws:states:${local.region}:${local.account}:execution:is459-g3-cloud-ingest:*" }
  ] })
}
