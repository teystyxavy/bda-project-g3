mock_provider "aws" {
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::588738581042:role/is459-g3-dashboard-status" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:ap-southeast-1:588738581042:log-group:test-status" }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:ap-southeast-1:588738581042:function:is459-g3-dashboard-status"
      invoke_arn = "arn:aws:apigateway:ap-southeast-1:lambda:path/2015-03-31/functions/arn:aws:lambda:ap-southeast-1:588738581042:function:is459-g3-dashboard-status/invocations"
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
run "public_read_only_status" {
  # Apply is entirely simulated by mock_provider.
  command = apply
  assert {
    condition     = aws_apigatewayv2_route.status.route_key == "GET /status" && aws_apigatewayv2_api.status.cors_configuration[0].allow_methods == toset(["GET"])
    error_message = "The public API exposes a single read-only GET route."
  }
  assert {
    condition = alltrue([for action in flatten([for statement in jsondecode(aws_iam_role_policy.function.policy).Statement : statement.Action]) :
    contains(["logs:CreateLogStream", "logs:PutLogEvents", "states:ListExecutions", "states:DescribeExecution", "states:GetExecutionHistory"], action)])
    error_message = "The status function may only read workflow status: no starts, stops, data or model access."
  }
  assert {
    condition     = aws_apigatewayv2_api.status.cors_configuration[0].allow_origins == toset([local.origin]) && aws_apigatewayv2_stage.status.default_route_settings[0].throttling_rate_limit == 5 && aws_lambda_function.status.reserved_concurrent_executions == 2
    error_message = "Restrict browser origins and bound public traffic and compute."
  }
  assert {
    condition     = output.public_configuration.status_url == "https://fixtureapi.execute-api.ap-southeast-1.amazonaws.com/status"
    error_message = "Publish the status endpoint for the dashboard."
  }
}
