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
