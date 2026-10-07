# =============================================================================
# infra/terraform/versions.tf -- Terraform/provider versions and AWS provider
# =============================================================================
# Pins Terraform 1.7.x-1.x and the AWS provider 6.x so every team member gets
# the same behaviour. backend "local" keeps the state file on the operator's
# machine (the deploy script passes its path at `terraform init`).

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
