mock_provider "aws" {}
run "upload_marker_starts_existing_batch_once" {
  command = plan
  assert {
    condition     = jsondecode(aws_cloudwatch_event_rule.raw_upload.event_pattern).detail.object.key[0].wildcard == "pipeline/runs/*/raw/trigger.json" && aws_s3_bucket_notification.eventbridge.eventbridge
    error_message = "Only the explicit trigger marker may start a run; manifest uploads from the CLI or cloud acquisition must not."
  }
  assert {
    condition     = local.batch == "arn:aws:states:ap-southeast-1:588738581042:stateMachine:is459-g3-ingest" && jsondecode(file("${path.module}/workflow.asl.json")).States.StartBatch.Parameters["Name.$"] == "$.trigger.run_id"
    error_message = "Reuse the deployed batch and name its execution after the run so a redelivered event cannot start a second run."
  }
  assert {
    condition     = !contains(keys(jsondecode(file("${path.module}/workflow.asl.json")).States.StartBatch), "Retry") && jsondecode(file("${path.module}/workflow.asl.json")).States.StartBatch.Resource == "arn:aws:states:::states:startExecution"
    error_message = "Do not automatically repeat paid batch executions."
  }
}
run "disabled_rule_ignores_uploads" {
  command = plan
  variables { enabled = false }
  assert {
    condition     = aws_cloudwatch_event_rule.raw_upload.state == "DISABLED"
    error_message = "The rule must be switchable off without removing the stack."
  }
}
