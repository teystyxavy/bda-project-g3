# =============================================================================
# infra/ingestion/versions.tf -- Terraform/provider pins for the ingestion stack
# =============================================================================
# Requires Terraform >= 1.9 (the `removed` block in main.tf needs >= 1.7).
# Own local state; provider locked to Group 3's account in Singapore; resources
# tagged Component = Ingestion.

terraform {
  required_version = ">= 1.9, < 2.0"
  backend "local" {}
  required_providers { aws = { source = "hashicorp/aws", version = "~> 6.0" } }
}
provider "aws" {
  region              = "ap-southeast-1"
  allowed_account_ids = ["588738581042"]
  default_tags { tags = { Project = "is459-g3", ManagedBy = "Terraform", Component = "Ingestion" } }
}
