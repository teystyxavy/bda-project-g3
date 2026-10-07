# =============================================================================
# infra/terraform/variables.tf -- inputs for the foundation stack
# =============================================================================
# Values are supplied by scripts/aws/plan-aws.ps1 (or -var / tfvars). Each
# validation block rejects bad input at plan time, before anything is created.
#
# aws_region: where everything is deployed (Singapore by default).

variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}

# aws_account_id: the 12-digit account we expect to deploy into. It is also
# used in versions.tf as allowed_account_ids, so Terraform refuses to run with
# credentials for any other account, and it makes the bucket name unique.
variable "aws_account_id" {
  type        = string
  description = "Expected deployment account; prevents applying to another account."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Use a 12-digit AWS account ID."
  }
}

# project_name: prefix for every resource name (bucket, job, workgroup ...).
# Limited to 3-20 lowercase characters so derived names stay within AWS limits
# (S3 bucket names max 63 chars, lowercase only).
variable "project_name" {
  type    = string
  default = "is459-g3"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.project_name))
    error_message = "Use 3–20 lowercase letters, digits or hyphens; start with a letter."
  }
}

# alert_email: who receives budget and Glue-failure e-mails. Marked sensitive
# so Terraform hides it in plan output; the regex is a basic format check.
variable "alert_email" {
  type        = string
  description = "Recipient for budget notices and Glue failures; confirm the SNS email."
  sensitive   = true
  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "Provide an email address."
  }
}

# monthly_budget_usd: AWS Budget limit used in main.tf. Validation enforces a
# hard ceiling of USD 10/month agreed for this student project.
variable "monthly_budget_usd" {
  type    = number
  default = 10
  validation {
    condition     = var.monthly_budget_usd > 0 && var.monthly_budget_usd <= 10
    error_message = "Budget must be positive and no higher than the user's USD 10 monthly maximum."
  }
}
