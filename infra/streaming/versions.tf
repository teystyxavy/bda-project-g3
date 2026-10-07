# =============================================================================
# infra/streaming/versions.tf -- Terraform/provider pins for the streaming demo
# =============================================================================
# Separate Terraform root with its own local state, so the demo can be created
# and destroyed without touching the batch pipeline stacks.

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

# allowed_account_ids prevents deploying with the wrong credentials; default
# tags label every resource Component = Streaming for cost tracking.
provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "is459-g3", ManagedBy = "Terraform", Component = "Streaming" }
  }
}
