output "state_machine_arn" { value = try(aws_sfn_state_machine.batch[0].arn, null) }
output "definition" { value = jsondecode(local.definition) }
output "runtime_policy" { value = local.permissions }
output "execution_input" {
  description = "Preparation only: explicit paid execution approval still required."
  value       = { run_id = var.run_id, expected_rows = 5078345, execution_approved = false }
}
