mock_provider "aws" {
  mock_resource "aws_cognito_user_pool" {
    defaults = { id = "ap-southeast-1_OperatorFixture" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::588738581042:role/is459-g3-dashboard-control" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-southeast-1:588738581042:log-group:test-control" }
  }
  mock_resource "aws_dynamodb_table" {
    defaults = { arn = "arn:aws:dynamodb:ap-southeast-1:588738581042:table/is459-g3-dashboard-launch" }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:ap-southeast-1:588738581042:function:is459-g3-dashboard-control"
      invoke_arn = "arn:aws:apigateway:ap-southeast-1:lambda:path/2015-03-31/functions/arn:aws:lambda:ap-southeast-1:588738581042:function:is459-g3-dashboard-control/invocations"
    }
  }
  mock_resource "aws_apigatewayv2_api" {
    defaults = {
      id            = "fixtureapi"
      api_endpoint  = "https://fixtureapi.execute-api.ap-southeast-1.amazonaws.com"
      execution_arn = "arn:aws:execute-api:ap-southeast-1:588738581042:fixtureapi"
    }
  }
}
variables {
  # Existing local file supplies a hash for mock plans; no Lambda is deployed.
  lambda_zip_path = "versions.tf"
}
run "protected_bounded_control_plane" {
  # Apply is entirely simulated by mock_provider; computed policy ARNs are known.
  command = apply
  assert {
    condition     = alltrue([for route in aws_apigatewayv2_route.control : route.authorization_type == "JWT" && contains(route.authorization_scopes, "ingestion/control")])
    error_message = "Every API route must require an access token with the control scope."
  }
  assert {
    condition     = aws_cognito_user_pool.operators.admin_create_user_config[0].allow_admin_create_user_only && !aws_cognito_user_pool_client.dashboard.generate_secret && aws_cognito_user_pool_client.dashboard.allowed_oauth_flows == toset(["code"])
    error_message = "Use invited accounts and a public authorization-code client, never implicit grants or browser secrets."
  }
  assert {
    condition     = aws_dynamodb_table.launch.billing_mode == "PAY_PER_REQUEST" && aws_lambda_function.control.timeout == 30 && aws_lambda_function.control.reserved_concurrent_executions == 2
    error_message = "Launch serialization and API compute must remain bounded and pay per use."
  }
  assert {
    condition     = aws_apigatewayv2_api.control.cors_configuration[0].allow_origins == toset([local.origin]) && aws_apigatewayv2_stage.control.default_route_settings[0].throttling_rate_limit == 2
    error_message = "Restrict browser origins and bound API traffic."
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.function.policy).Statement[1].Resource == local.machine && !strcontains(aws_iam_role_policy.function.policy, "s3:") && !strcontains(aws_iam_role_policy.function.policy, "sagemaker:")
    error_message = "The API may launch only the existing acquisition workflow and must not read raw data or operate model jobs directly."
  }
}
