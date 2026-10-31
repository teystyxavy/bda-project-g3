# =============================================================================
# infra/tfstate-bootstrap/main.tf -- shared Terraform remote-state backend
# =============================================================================
# What it is: the one stack every other infra/ stack's "s3" backend points
# at. Deploy and apply this first, with its state kept locally (see
# versions.tf) -- then point every other stack's `terraform init` at the
# bucket/table created here instead of a per-machine local state file.
#
# What it creates:
#   * A private, versioned, encrypted S3 bucket holding one state object per
#     stack, under keys like "foundation/terraform.tfstate",
#     "ingestion/terraform.tfstate", etc.
#   * A DynamoDB table used for state locking (classic lock-item protocol,
#     not native S3 locking -- see the version note in versions.tf).
#
# How it is run: same pattern as the other stacks (terraform plan, a
# human-approved apply), but only ever once per migration, and only from one
# machine at a time -- creating the state backend itself is exactly the kind
# of operation that must not race.

locals {
  # bucket_name deliberately does NOT reuse infra/terraform's
  # "${project_name}-${account}-${region}" pattern: that name is the pipeline
  # DATA bucket. State needs its own bucket so a future data-bucket
  # cleanup/destroy can never touch Terraform's own state.
  bucket_name = "${var.project_name}-tfstate-${var.aws_account_id}"
  table_name  = "${var.project_name}-tfstate-lock"
}

# The state bucket. force_destroy = false: `terraform destroy` on THIS stack
# must refuse to delete it while any other stack's state object still lives
# inside it -- losing state accidentally is far worse than losing data here.
resource "aws_s3_bucket" "state" {
  bucket        = local.bucket_name
  force_destroy = false
  lifecycle { prevent_destroy = true }
}

# No public access, ever: state can contain resource ARNs, IDs and (for some
# resource types) sensitive attribute values.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Default encryption at rest (SSE-S3 / AES256) -- free, no KMS key to manage.
resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# Versioning is the actual safety net here: every `terraform apply` overwrites
# the state object, so this is what lets a bad push be rolled back by
# restoring the previous object version. No lifecycle rule expires old
# versions -- state objects are tiny, so keeping full history costs nothing
# worth trimming.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

# Same TLS-only bucket policy as the pipeline data bucket in infra/terraform.
resource "aws_s3_bucket_policy" "tls" {
  bucket = aws_s3_bucket.state.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport", Effect = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# Lock table: one item per state key, written for the duration of a plan/
# apply/destroy and deleted when it finishes. PAY_PER_REQUEST billing because
# this table sees bursty, infrequent traffic (one write per terraform
# operation) -- provisioned capacity would just be idle most of the time.
resource "aws_dynamodb_table" "lock" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  lifecycle { prevent_destroy = true }
  attribute {
    name = "LockID"
    type = "S"
  }
}
