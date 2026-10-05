variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}

variable "aws_account_id" {
  type        = string
  description = "Expected deployment account; prevents applying to another account."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Use a 12-digit AWS account ID."
  }
}

variable "project_name" {
  type    = string
  default = "is459-g3"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.project_name))
    error_message = "Use 3–20 lowercase letters, digits or hyphens; start with a letter."
  }
}

variable "alert_email" {
  type        = string
  description = "Recipient for budget notices and Glue failures; confirm the SNS email."
  sensitive   = true
  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "Provide an email address."
  }
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
  validation {
    condition     = var.monthly_budget_usd > 0 && var.monthly_budget_usd <= 10
    error_message = "Budget must be positive and no higher than the user's USD 10 monthly maximum."
  }
}
