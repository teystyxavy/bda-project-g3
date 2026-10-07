# =============================================================================
# infra/hosting/versions.tf -- Terraform/provider pins for hosting
# =============================================================================
# Own Terraform root with local state; provider locked to var.aws_account_id;
# resources tagged Component = Dashboard.

terraform {
  required_version = ">= 1.9, < 2.0"
  backend "local" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "is459-g3", ManagedBy = "Terraform", Component = "Dashboard" }
  }
}
