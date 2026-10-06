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
variable "cognito_domain_prefix" {
  type    = string
  default = "is459-g3-ingestion-588738581042"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,62}$", var.cognito_domain_prefix))
    error_message = "Use a globally unique lowercase Cognito domain prefix."
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

# Invitation-only, public browser client. Passwords/users are never Terraform inputs.
resource "aws_cognito_user_pool" "operators" {
  name                     = "is459-g3-ingestion-operators"
  user_pool_tier           = "LITE"
  deletion_protection      = "ACTIVE"
  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]
  admin_create_user_config { allow_admin_create_user_only = true }
  password_policy {
    minimum_length                   = 12
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }
  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration { enabled = true }
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
}
resource "aws_cognito_user_group" "operators" {
  name         = "ingestion-operators"
  user_pool_id = aws_cognito_user_pool.operators.id
  description  = "Invited Group 3 members allowed to launch bounded ingestion runs."
}
resource "aws_cognito_resource_server" "control" {
  identifier   = "ingestion"
  name         = "Ingestion control"
  user_pool_id = aws_cognito_user_pool.operators.id
  scope {
    scope_name        = "control"
    scope_description = "Start and inspect operator-owned ingestion runs."
  }
}
resource "aws_cognito_user_pool_client" "dashboard" {
  name                                 = "is459-g3-dashboard"
  user_pool_id                         = aws_cognito_user_pool.operators.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "ingestion/control"]
  supported_identity_providers         = ["COGNITO"]
  callback_urls                        = [var.dashboard_url]
  logout_urls                          = [var.dashboard_url]
  prevent_user_existence_errors        = "ENABLED"
  enable_token_revocation              = true
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }
  depends_on = [aws_cognito_resource_server.control]
}
resource "aws_cognito_user_pool_domain" "operators" {
  domain       = var.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.operators.id
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
    { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:PutItem"], Resource = aws_dynamodb_table.launch.arn }
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
resource "aws_apigatewayv2_authorizer" "operators" {
  api_id           = aws_apigatewayv2_api.control.id
  name             = "invited-operators"
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.dashboard.id]
    issuer   = "https://cognito-idp.${local.region}.amazonaws.com/${aws_cognito_user_pool.operators.id}"
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
  for_each             = toset(["GET /options", "GET /runs", "POST /runs", "GET /runs/{run_id}"])
  api_id               = aws_apigatewayv2_api.control.id
  route_key            = each.value
  target               = "integrations/${aws_apigatewayv2_integration.control.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.operators.id
  authorization_scopes = ["ingestion/control"]
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
output "user_pool_id" { value = aws_cognito_user_pool.operators.id }
output "api_url" { value = aws_apigatewayv2_api.control.api_endpoint }
output "public_configuration" {
  description = "Public identifiers only. Publish as dashboard/ingestion-config.json after review."
  value = {
    schema_version = 1
    api_url        = aws_apigatewayv2_api.control.api_endpoint
    cognito_domain = "https://${aws_cognito_user_pool_domain.operators.domain}.auth.${local.region}.amazoncognito.com"
    client_id      = aws_cognito_user_pool_client.dashboard.id
    redirect_uri   = var.dashboard_url
  }
}
