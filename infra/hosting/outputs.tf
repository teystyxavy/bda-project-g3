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
