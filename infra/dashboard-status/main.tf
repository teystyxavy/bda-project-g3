variable "lambda_zip_path" {
  type        = string
  description = "Local artifact produced by scripts/aws/prepare-dashboard-status.py."
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
  account = "588738581042"
  region  = "ap-southeast-1"
  origin  = trimsuffix(var.dashboard_url, "/")
  machine = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-cloud-ingest"
  batch   = "arn:aws:states:${local.region}:${local.account}:stateMachine:is459-g3-ingest"
  executions = [for name in ["is459-g3-cloud-ingest", "is459-g3-ingest"] :
  "arn:aws:states:${local.region}:${local.account}:execution:${name}:*"]
}

# Public and read-only: one GET route, no sign-in, no start/stop or data access.
resource "aws_cloudwatch_log_group" "function" {
  name              = "/aws/lambda/is459-g3-dashboard-status"
  retention_in_days = 14
}
resource "aws_iam_role" "function" {
  name               = "is459-g3-dashboard-status"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "function" {
  role = aws_iam_role.function.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.function.arn}:*" },
    { Effect = "Allow", Action = ["states:ListExecutions"], Resource = local.machine },
    { Effect = "Allow", Action = ["states:DescribeExecution", "states:GetExecutionHistory"], Resource = local.executions }
  ] })
}
resource "aws_lambda_function" "status" {
  function_name                  = "is459-g3-dashboard-status"
  role                           = aws_iam_role.function.arn
  runtime                        = "python3.12"
  handler                        = "dashboard_status.handler"
  filename                       = var.lambda_zip_path
  source_code_hash               = filebase64sha256(var.lambda_zip_path)
  memory_size                    = 128
  timeout                        = 15
  reserved_concurrent_executions = 2
  environment {
    variables = {
      ACQUISITION_MACHINE = local.machine
      BATCH_MACHINE       = local.batch
      DASHBOARD_URL       = var.dashboard_url
    }
  }
  depends_on = [aws_iam_role_policy.function, aws_cloudwatch_log_group.function]
}
resource "aws_apigatewayv2_api" "status" {
  name          = "is459-g3-dashboard-status"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = [local.origin]
    allow_methods = ["GET"]
    max_age       = 300
  }
}
resource "aws_apigatewayv2_integration" "status" {
  api_id                 = aws_apigatewayv2_api.status.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.status.invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 15000
}
resource "aws_apigatewayv2_route" "status" {
  api_id             = aws_apigatewayv2_api.status.id
  route_key          = "GET /status"
  target             = "integrations/${aws_apigatewayv2_integration.status.id}"
  authorization_type = "NONE"
}
resource "aws_lambda_permission" "api" {
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.status.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.status.execution_arn}/*/GET/status"
}
resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/is459-g3-dashboard-status"
  retention_in_days = 14
}
resource "aws_apigatewayv2_stage" "status" {
  api_id      = aws_apigatewayv2_api.status.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = 10
    throttling_rate_limit  = 5
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({ requestId = "$context.requestId", routeKey = "$context.routeKey",
    status = "$context.status", responseLength = "$context.responseLength" })
  }
}
output "api_url" { value = aws_apigatewayv2_api.status.api_endpoint }
output "public_configuration" {
  description = "Public endpoint only. Publish as dashboard/status-config.json after review."
  value = {
    schema_version = 1
    status_url     = "${aws_apigatewayv2_api.status.api_endpoint}/status"
  }
}
