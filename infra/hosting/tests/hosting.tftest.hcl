mock_provider "aws" {}

variables {
  aws_account_id = "123456789012"
}

run "default_creates_nothing" {
  command = plan
  assert {
    condition     = length(aws_s3_bucket.dashboard) == 0 && length(aws_cloudfront_distribution.dashboard) == 0
    error_message = "Default must create no hosting resources."
  }
}

run "staging_remains_disabled_and_private" {
  command = apply # Mock provider only: no AWS calls or cloud resources.
  variables { provision_hosting = true }
  assert {
    condition     = !aws_cloudfront_distribution.dashboard[0].enabled && !aws_s3_bucket.dashboard[0].force_destroy
    error_message = "Staging must not publish or automatically delete data."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.dashboard[0].block_public_acls && aws_s3_bucket_public_access_block.dashboard[0].block_public_policy && aws_s3_bucket_public_access_block.dashboard[0].ignore_public_acls && aws_s3_bucket_public_access_block.dashboard[0].restrict_public_buckets
    error_message = "All four public-access blocks are required."
  }
  assert {
    condition     = one(aws_cloudfront_distribution.dashboard[0].origin).origin_path == "/dashboard" && aws_cloudfront_origin_access_control.dashboard[0].signing_behavior == "always"
    error_message = "Use only the dashboard prefix and signed S3 requests."
  }
  assert {
    condition     = aws_cloudfront_distribution.dashboard[0].default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https" && toset(aws_cloudfront_distribution.dashboard[0].default_cache_behavior[0].allowed_methods) == toset(["GET", "HEAD"])
    error_message = "Only HTTPS viewer reads are allowed."
  }
  assert {
    condition     = output.dashboard_bucket_policy.Statement[0].Action == "s3:GetObject" && output.dashboard_bucket_policy.Statement[0].Resource == "${aws_s3_bucket.dashboard[0].arn}/dashboard/*" && output.dashboard_bucket_policy.Statement[0].Condition.StringEquals["AWS:SourceArn"] == aws_cloudfront_distribution.dashboard[0].arn && output.dashboard_bucket_policy.Statement[1].Effect == "Deny"
    error_message = "Read access must be scoped to this distribution and dashboard prefix, with TLS deny."
  }
  assert {
    condition     = one(aws_s3_bucket_ownership_controls.dashboard[0].rule).object_ownership == "BucketOwnerEnforced" && one(aws_s3_bucket_server_side_encryption_configuration.dashboard[0].rule).apply_server_side_encryption_by_default[0].sse_algorithm == "AES256"
    error_message = "Disable ACLs and encrypt dashboard storage."
  }
}

run "reject_unapproved_publication" {
  command = plan
  variables {
    provision_hosting = true
    publish_dashboard = true
  }
  expect_failures = [var.publish_dashboard]
}

run "approved_publication_gate" {
  command = plan
  variables {
    provision_hosting    = true
    publish_dashboard    = true
    public_demo_approved = true
  }
  assert {
    condition     = aws_cloudfront_distribution.dashboard[0].enabled
    error_message = "Publication requires explicit opt-in."
  }
}
