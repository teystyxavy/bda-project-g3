# =============================================================================
# infra/tfstate-bootstrap/variables.tf -- inputs for the state-backend stack
# =============================================================================

# aws_region: where the state bucket and lock table live. Should match the
# region the other stacks deploy into, though S3 bucket names are global.
variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}

# aws_account_id: the 12-digit account this must deploy into. Also used as
# allowed_account_ids in versions.tf and folded into the bucket name.
variable "aws_account_id" {
  type        = string
  description = "Expected deployment account; prevents applying to another account."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Use a 12-digit AWS account ID."
  }
}

# project_name: prefix for the bucket and lock-table names. Same pattern and
# limits as every other stack's project_name variable.
variable "project_name" {
  type    = string
  default = "is459-g3"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.project_name))
    error_message = "Use 3-20 lowercase letters, digits or hyphens; start with a letter."
  }
}
