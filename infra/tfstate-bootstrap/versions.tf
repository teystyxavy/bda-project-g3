# =============================================================================
# infra/tfstate-bootstrap/versions.tf -- Terraform/provider versions
# =============================================================================
# The existing backend was recovered by importing its six live resources.
# Its state is now shared in the same versioned bucket; never destroy the
# backend while any project state remains there. See the recovery runbook.

terraform {
  required_version = ">= 1.7, < 2.0"
  backend "s3" {}
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
