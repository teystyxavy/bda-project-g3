# =============================================================================
# infra/orchestration/outputs.tf -- values for review and for starting a run
# =============================================================================
# state_machine_arn: null when orchestration is not provisioned.
# definition: the rendered workflow JSON, so reviewers can read the exact states.
# runtime_policy: the IAM permissions the workflow role receives.
# execution_input: template input for a manual StartExecution; it deliberately
#   sets execution_approved = false so someone must consciously approve a run.

output "state_machine_arn" { value = try(aws_sfn_state_machine.batch[0].arn, null) }
output "definition" { value = jsondecode(local.definition) }
output "runtime_policy" { value = local.permissions }
output "execution_input" {
  description = "Preparation only: explicit paid execution approval still required."
  value       = { run_id = var.run_id, expected_rows = 5078345, execution_approved = false }
}
