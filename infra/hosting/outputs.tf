# =============================================================================
# infra/hosting/outputs.tf
# =============================================================================
# dashboard_bucket: where releases are uploaded (null if not provisioned).
# distribution_id: needed for cache invalidations after a publish (also stored
#   in pipeline_config for the ingestion control Lambda).
# published_url: the public https://<id>.cloudfront.net/ address, only when
#   publishing is enabled.
# dashboard_bucket_policy: the bucket policy JSON for review.

output "dashboard_bucket" {
  value = try(aws_s3_bucket.dashboard[0].id, null)
}

output "distribution_id" {
  value = try(aws_cloudfront_distribution.dashboard[0].id, null)
}

output "published_url" {
  value = var.publish_dashboard ? "https://${aws_cloudfront_distribution.dashboard[0].domain_name}/" : null
}

output "dashboard_bucket_policy" {
  description = "Review the complete two-statement resource policy before deployment."
  value       = try(jsondecode(aws_s3_bucket_policy.dashboard[0].policy), null)
}
