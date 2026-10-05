locals {
  count       = var.provision_hosting ? 1 : 0
  bucket_name = "is459-g3-dashboard-${var.aws_account_id}-${var.aws_region}"
}

resource "aws_s3_bucket" "dashboard" {
  count         = local.count
  bucket        = local.bucket_name
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "dashboard" {
  count                   = local.count
  bucket                  = aws_s3_bucket.dashboard[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "dashboard" {
  count  = local.count
  bucket = aws_s3_bucket.dashboard[0].id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dashboard" {
  count  = local.count
  bucket = aws_s3_bucket.dashboard[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_versioning" "dashboard" {
  count  = local.count
  bucket = aws_s3_bucket.dashboard[0].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_cloudfront_origin_access_control" "dashboard" {
  count                             = local.count
  name                              = "is459-g3-dashboard-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

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
      {
        Sid       = "AllowSpecificCloudFrontDashboardRead"
        Effect    = "Allow"
        Principal = { Service = "cloudfront.amazonaws.com" }
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.dashboard[0].arn}/dashboard/*"
        Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.dashboard[0].arn } }
      },
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
