# =============================================================================
# infra/tfstate-bootstrap/versions.tf -- Terraform/provider versions
# =============================================================================
# This stack creates the S3 bucket + DynamoDB lock table that every OTHER
# stack's "s3" backend points at. It can never store its own state there
# (chicken-and-egg), so backend "local" here is permanent, not a placeholder
# to migrate later like the other stacks.

terraform {
  required_version = ">= 1.7, < 2.0"
  backend "local" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

# AWS provider configuration:
#   allowed_account_ids -- safety net: abort if the credentials belong to any
#     account other than var.aws_account_id;
#   default_tags -- every resource is tagged Project/ManagedBy so costs can be
#     filtered in Cost Explorer and Terraform-owned resources are obvious.
provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = var.project_name, ManagedBy = "Terraform" }
  }
}
