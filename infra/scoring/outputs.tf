# =============================================================================
# infra/scoring/outputs.tf
# =============================================================================
# scoring_job: job name (null when not provisioned).
# runtime_permissions: the full IAM policy, shown even when nothing is
#   provisioned, so it can be reviewed before approval.
# run_output: S3 location where this run's scores will be written.

output "scoring_job" {
  value = try(aws_glue_job.scoring[0].name, null)
}
output "runtime_permissions" {
  description = "Complete runtime policy for review; generated even when provisioning is disabled."
  value       = local.permissions
}
output "run_output" {
  value = "s3://${local.bucket}/${local.output_prefix}/"
}
