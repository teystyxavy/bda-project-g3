# =============================================================================
# infra/hosting/main.tf -- static dashboard hosting (S3 + CloudFront)
# =============================================================================
# What it is: where the project's results dashboard (BQ1 review queue + BQ2
# mule networks) is published. The dashboard is static HTML/JS/CSS + JSON
# snapshots, so no web server is needed:
#   * a private S3 bucket is459-g3-dashboard-<account>-<region>; files live
#     under dashboard/ (written by the ingestion control Lambda's "publish"
#     step: dashboard/index.html, dashboard.js, dashboard.css, current.json
#     and releases/<run_id>/...);
#   * a CloudFront distribution serves ONLY the dashboard/ folder over HTTPS,
#     reading the bucket through Origin Access Control (OAC), so the bucket
#     itself never becomes public.
# Only reviewed, synthetic/aggregated output is published -- no raw data or
# model artefacts.
#
# Gating: provision_hosting creates the stack; publish_dashboard (needs
# public_demo_approved) actually enables the public CloudFront distribution.

locals {
  # count: on/off switch; bucket_name: dashboard bucket (separate from the data
  # lake so a mistake here can never expose pipeline data).
  count       = var.provision_hosting ? 1 : 0
  bucket_name = "is459-g3-dashboard-${var.aws_account_id}-${var.aws_region}"
}

# Dashboard bucket. force_destroy = false protects published releases.
resource "aws_s3_bucket" "dashboard" {
  count         = local.count
  bucket        = local.bucket_name
  force_destroy = false
}

# Block all public access: visitors reach files only through CloudFront.
resource "aws_s3_bucket_public_access_block" "dashboard" {
  count                   = local.count
  bucket                  = aws_s3_bucket.dashboard[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# BucketOwnerEnforced disables ACLs entirely; access is controlled only by the
# bucket policy below.
resource "aws_s3_bucket_ownership_controls" "dashboard" {
  count  = local.count
  bucket = aws_s3_bucket.dashboard[0].id
  rule { object_ownership = "BucketOwnerEnforced" }
}

# Encrypt objects at rest with S3-managed keys.
resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  count  = local.count
  bucket = aws_s3_bucket.dashboard[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Versioning: older dashboard files can be restored after a bad publish.
resource "aws_s3_bucket_versioning" "dashboard" {
  count  = local.count
  bucket = aws_s3_bucket.dashboard[0].id
  versioning_configuration { status = "Enabled" }
}

# Origin Access Control: CloudFront signs every request to S3 with SigV4, so
# the bucket policy can allow "CloudFront, for this distribution only".
resource "aws_cloudfront_origin_access_control" "dashboard" {
  count                             = local.count
  name                              = "is459-g3-dashboard-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Security headers added to every response:
#   X-Content-Type-Options: nosniff; X-Frame-Options: DENY (no embedding,
#   prevents click-jacking); Referrer-Policy: no-referrer; and HSTS for one
#   year (browsers must always use HTTPS).
resource "aws_cloudfront_response_headers_policy" "dashboard" {
  count = local.count
  name  = "is459-g3-dashboard-headers"
  security_headers_config {
    content_type_options { override = true }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "no-referrer"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
  }
}

# The CloudFront distribution (CDN). enabled only when publish_dashboard is
# true. PriceClass_100 = cheapest edge locations (North America/Europe);
# index.html is served for "/". The origin_path "/dashboard" means only the
# dashboard/ folder of the bucket is reachable.
resource "aws_cloudfront_distribution" "dashboard" {
  count               = local.count
  enabled             = var.publish_dashboard
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  comment             = "Reviewed synthetic IS459 demo; no raw data or model artifacts"
  price_class         = "PriceClass_100"
  wait_for_deployment = true
  origin {
    domain_name              = aws_s3_bucket.dashboard[0].bucket_regional_domain_name
    origin_id                = "dashboard-only"
    origin_path              = "/dashboard"
    origin_access_control_id = aws_cloudfront_origin_access_control.dashboard[0].id
  }
  # Cache behaviour: redirect HTTP to HTTPS; read-only (GET/HEAD); compress;
  # cache for 5 minutes by default (max 1 hour) so a new release appears
  # quickly; query strings and cookies are ignored (better cache hit rate).
  default_cache_behavior {
    target_origin_id           = "dashboard-only"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    min_ttl                    = 0
    default_ttl                = 300
    max_ttl                    = 3600
    response_headers_policy_id = aws_cloudfront_response_headers_policy.dashboard[0].id
    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }
  # No geographic restrictions; use the default *.cloudfront.net certificate
  # (no custom domain).
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate { cloudfront_default_certificate = true }
}

# Complete resource policy: read only the dashboard prefix through this OAC;
# deny insecure transport. No identity-based deployment policy is created here.
resource "aws_s3_bucket_policy" "dashboard" {
  count  = local.count
  bucket = aws_s3_bucket.dashboard[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Statement 1: allow s3:GetObject on dashboard/* only, only for the CloudFront
      # service, and only when the request comes from THIS distribution
      # (AWS:SourceArn). Everything else in the bucket stays unreadable.
      {
        Sid       = "AllowSpecificCloudFrontDashboardRead"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.dashboard[0].arn}/dashboard/*"
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.dashboard[0].arn } }
      },
      # Statement 2: deny any non-HTTPS request to the bucket or its objects.
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.dashboard[0].arn, "${aws_s3_bucket.dashboard[0].arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      }
    ]
  })
  depends_on = [aws_s3_bucket_public_access_block.dashboard]
}
