# =============================================================================
# infra/eventbridge-trigger/versions.tf -- Terraform/provider pins
# =============================================================================
# Own Terraform root with shared S3 state (key selected by
# init-terraform.ps1). Provider locked to Group 3's account in
# Singapore; resources tagged Component = EventBridgeTrigger.

terraform {
  required_version = ">= 1.9, < 2.0"
  backend "s3" {}
  required_providers { aws = { source = "hashicorp/aws", version = "~> 6.0" } }
}
provider "aws" {
  region              = "ap-southeast-1"
  allowed_account_ids = ["588738581042"]
  default_tags { tags = { Project = "is459-g3", ManagedBy = "Terraform", Component = "EventBridgeTrigger" } }
}
