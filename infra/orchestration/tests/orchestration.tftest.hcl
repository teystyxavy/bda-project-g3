mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::588738581042:role/mock-reviewed-role" } }
  mock_resource "aws_scheduler_schedule_group" { defaults = { arn = "arn:aws:scheduler:ap-southeast-1:588738581042:schedule-group/is459-g3-reviewed-batch" } }
}
variables { aws_account_id = "588738581042" }
run "default_creates_nothing" {
  command = plan
  assert {
    condition     = length(aws_sfn_state_machine.batch) == 0 && length(aws_iam_role.workflow) == 0 && length(aws_scheduler_schedule.batch) == 0
    error_message = "Default must not create or schedule anything."
  }
}
run "manual_workflow_without_schedule" {
  command = apply
  variables { provision_orchestration = true }
  assert {
    condition     = aws_sfn_state_machine.batch[0].type == "STANDARD" && length(aws_scheduler_schedule.batch) == 0 && !output.execution_input.execution_approved
    error_message = "Use Standard with no scheduled or approved execution."
  }
  assert {
    condition     = jsondecode(aws_iam_role.workflow[0].assume_role_policy).Statement[0].Condition.ArnEquals["aws:SourceArn"] == local.machine_arn && length([for statement in output.runtime_policy.Statement : statement if contains(statement.Resource, "*")]) == 0
    error_message = "Trust and resources must be scoped."
  }
}
run "optional_schedule_disabled" {
  command = apply
  variables {
    provision_orchestration = true
    provision_schedule      = true
    scheduled_at            = "2099-01-01T00:00:00"
  }
  assert {
    condition     = aws_scheduler_schedule.batch[0].state == "DISABLED" && jsondecode(aws_scheduler_schedule.batch[0].target[0].input).Name == var.run_id && aws_scheduler_schedule.batch[0].target[0].retry_policy[0].maximum_retry_attempts == 0
    error_message = "Schedule must stay disabled, deduplicate by name, and not retry compute."
  }
  assert {
    condition     = jsondecode(aws_iam_role.scheduler[0].assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceArn"] == aws_scheduler_schedule_group.batch[0].arn
    error_message = "Scheduler trust must use the schedule-group ARN."
  }
}
run "reject_unapproved_schedule" {
  command = plan
  variables {
    provision_orchestration = true
    provision_schedule      = true
    scheduled_at            = "2099-01-01T00:00:00"
    enable_schedule         = true
  }
  expect_failures = [var.enable_schedule]
}
run "reject_schedule_without_time" {
  command = plan
  variables {
    provision_orchestration = true
    provision_schedule      = true
  }
  expect_failures = [var.provision_schedule]
}
