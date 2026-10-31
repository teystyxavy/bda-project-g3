# =============================================================================
# infra/sagemaker/versions.tf -- Terraform/provider pins for SageMaker support
# =============================================================================
# Own Terraform root with shared S3 state; provider is hard-wired to Group 3's
# account in Singapore and tags resources Component = SageMakerTraining.

terraform {
  required_version = ">= 1.9, < 2.0"
  backend "s3" {}
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}
provider "aws" {
  region              = "ap-southeast-1"
  allowed_account_ids = ["588738581042"]
  default_tags {
    tags = { Project = "is459-g3", ManagedBy = "Terraform", Component = "SageMakerTraining" }
  }
}
