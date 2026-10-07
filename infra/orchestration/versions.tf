# =============================================================================
# infra/orchestration/versions.tf -- Terraform/provider pins for orchestration
# =============================================================================
# Own Terraform root with local state. The provider refuses any account other
# than var.aws_account_id and tags resources Component = Orchestration.

terraform {
  required_version = ">= 1.9, < 2.0"
  backend "local" {}
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "is459-g3", ManagedBy = "Terraform", Component = "Orchestration" }
  }
}
