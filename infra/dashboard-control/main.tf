variable "lambda_zip_path" {
  type        = string
  description = "Local artifact produced by scripts/aws/prepare-dashboard-api.py."
}
variable "dashboard_url" {
  type    = string
  default = "https://d1n61tlw9pcrog.cloudfront.net/"
  validation {
    condition     = can(regex("^https://[a-z0-9]+\\.cloudfront\\.net/$", var.dashboard_url))
    error_message = "Use the existing dashboard HTTPS CloudFront root URL with a trailing slash."
  }
}
locals {
  account         = "588738581042"
  region          = "ap-southeast-1"
  origin          = trimsuffix(var.dashboard_url, "/")
  machine         = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-cloud-ingest"
  batch           = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-ingest"
  pipeline_bucket = "is459-g3-${local.account}-${local.region}"
  executions = [for name in ["is459-g3-cloud-ingest", "is459-g3-ingest"] :
  "arn:aws:states:${local.region}:${local.account}:execution:${name}:*"]
}

resource "aws_dynamodb_table" "launch" {
  name         = "is459-g3-dashboard-launch"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  server_side_encryption { enabled = true }
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
resource "aws_cloudwatch_log_group" "function" {
  name              = "/aws/lambda/is459-g3-dashboard-control"
  retention_in_days = 14
}
resource "aws_iam_role" "function" {
  name               = "is459-g3-dashboard-control"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "function" {
  role = aws_iam_role.function.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.function.arn}:*" },
    { Effect = "Allow", Action = ["states:StartExecution"], Resource = local.machine },
    { Effect = "Allow", Action = ["states:ListExecutions"], Resource = [local.machine, local.batch] },
    { Effect = "Allow", Action = ["states:DescribeExecution", "states:GetExecutionHistory"], Resource = local.executions },
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"], Resource = aws_dynamodb_table.launch.arn },
    # Read-only "what already exists" inventory for the Ingestion tab: cached Kaggle
    # versions, published result-cache entries and their run manifests. No raw data.
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${local.pipeline_bucket}", Condition = { StringLike = { "s3:prefix" = ["pipeline/acquisition/versions/", "pipeline/acquisition/HI-Medium/versions/", "pipeline/cache/"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = [for key in ["pipeline/deployment.json", "pipeline/acquisition/latest.json", "pipeline/acquisition/HI-Medium/latest.json", "pipeline/cache/*", "pipeline/runs/*/raw/manifest.json"] : "arn:aws:s3:::${local.pipeline_bucket}/${key}"] }
  ] })
}
resource "aws_lambda_function" "control" {
  function_name                  = "is459-g3-dashboard-control"
  role                           = aws_iam_role.function.arn
  runtime                        = "python3.12"
  handler                        = "dashboard_api.handler"
  filename                       = var.lambda_zip_path
  source_code_hash               = filebase64sha256(var.lambda_zip_path)
  memory_size                    = 256
  timeout                        = 30
  reserved_concurrent_executions = 2
  environment {
    variables = {
      ACQUISITION_MACHINE = local.machine
      BATCH_MACHINE       = local.batch
      LOCK_TABLE          = aws_dynamodb_table.launch.name
      DASHBOARD_URL       = var.dashboard_url
    }
  }
  depends_on = [aws_iam_role_policy.function, aws_cloudwatch_log_group.function]
}
resource "aws_apigatewayv2_api" "control" {
  name          = "is459-g3-dashboard-control"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = [local.origin]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 300
  }
}
resource "aws_apigatewayv2_integration" "control" {
  api_id                 = aws_apigatewayv2_api.control.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.control.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 30000
}
resource "aws_apigatewayv2_route" "control" {
  for_each           = toset(["GET /options", "GET /runs", "POST /runs", "GET /runs/{run_id}", "POST /session/bootstrap", "POST /session/exchange", "POST /session/logout"])
  api_id             = aws_apigatewayv2_api.control.id
  route_key          = each.value
  target             = "integrations/${aws_apigatewayv2_integration.control.id}"
  authorization_type = each.value == "POST /session/bootstrap" ? "AWS_IAM" : "NONE"
}
resource "aws_lambda_permission" "api" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.control.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.control.execution_arn}/*/*/*"
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/is459-g3-dashboard-control"
  retention_in_days = 14
}
resource "aws_apigatewayv2_stage" "control" {
  api_id      = aws_apigatewayv2_api.control.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 4
    throttling_rate_limit  = 2
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({ requestId = "$context.requestId", routeKey = "$context.routeKey",
    status = "$context.status", responseLength = "$context.responseLength" })
  }
}
resource "aws_cloudwatch_metric_alarm" "errors" {
  alarm_name          = "is459-g3-dashboard-control-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.control.function_name }
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = ["arn:aws:sns:${local.region}:${local.account}:is459-g3-failures"]
}
output "api_url" { value = aws_apigatewayv2_api.control.api_endpoint }
output "public_configuration" {
  description = "Public identifiers only. Publish as dashboard/ingestion-config.json after review."
  value = {
    schema_version = 1
    api_url        = aws_apigatewayv2_api.control.api_endpoint
    auth_mode      = "aws-iam-helper"
    client_id      = "is459-g3-aws-operator"
    redirect_uri   = var.dashboard_url
  }
}
