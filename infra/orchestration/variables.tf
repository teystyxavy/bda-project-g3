variable "aws_account_id" {
  type = string
  validation {
    condition     = var.aws_account_id == "588738581042"
    error_message = "This reviewed workflow is scoped to Group 3's account."
  }
}
variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
  validation {
    condition     = var.aws_region == "ap-southeast-1"
    error_message = "Use the verified Singapore jobs and catalog."
  }
}
variable "provision_orchestration" {
  type    = bool
  default = false
}
variable "run_id" {
  type    = string
  default = "full-orchestrated-20261006"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,49}$", var.run_id))
    error_message = "Use a fresh 3–50-character lowercase alphanumeric/hyphen run ID."
  }
}
variable "provision_schedule" {
  type    = bool
  default = false
  validation {
    condition     = !var.provision_schedule || (var.provision_orchestration && var.scheduled_at != null)
    error_message = "Scheduling needs orchestration and a reviewed one-off UTC time."
  }
}
variable "scheduled_at" {
  description = "One-off UTC time YYYY-MM-DDTHH:MM:SS; no daily repeated processing."
  type        = string
  default     = null
  validation {
    condition     = var.scheduled_at == null ? true : can(formatdate("YYYY-MM-DD'T'hh:mm:ss", "${var.scheduled_at}Z"))
    error_message = "Supply a valid UTC date/time without a timezone suffix."
  }
}
variable "enable_schedule" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_schedule || (var.provision_schedule && var.scheduled_execution_approved)
    error_message = "Enabling a paid scheduled run requires explicit separate execution approval."
  }
}
variable "scheduled_execution_approved" {
  type    = bool
  default = false
}
