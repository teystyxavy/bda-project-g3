# =============================================================================
# infra/orchestration/variables.tf -- inputs and safety gates for orchestration
# =============================================================================
# aws_account_id: must be Group 3's account (no default, so it is always given
# explicitly).

variable "aws_account_id" {
  type = string
  validation {
    condition     = var.aws_account_id == "588738581042"
    error_message = "This reviewed workflow is scoped to Group 3's account."
  }
}
# aws_region: locked to Singapore, where the Glue job and catalog already live.
variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
  validation {
    condition     = var.aws_region == "ap-southeast-1"
    error_message = "Use the verified Singapore jobs and catalog."
  }
}
# provision_orchestration: master switch; false (default) creates nothing.
variable "provision_orchestration" {
  type    = bool
  default = false
}
# run_id: unique id for this run; names the S3 output prefix, the schedule and
# the Step Functions execution, so re-using an id cannot overwrite old output.
variable "run_id" {
  type    = string
  default = "full-orchestrated-20261006"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,49}$", var.run_id))
    error_message = "Use a fresh 3–50-character lowercase alphanumeric/hyphen run ID."
  }
}
# provision_schedule: create the one-off Scheduler resources. Needs
# orchestration enabled and a scheduled_at time.
variable "provision_schedule" {
  type    = bool
  default = false
  validation {
    condition     = !var.provision_schedule || (var.provision_orchestration && var.scheduled_at != null)
    error_message = "Scheduling needs orchestration and a reviewed one-off UTC time."
  }
}
# scheduled_at: the single UTC time to start the run (no daily repetition);
# validated by trying to parse it as a timestamp.
variable "scheduled_at" {
  description = "One-off UTC time YYYY-MM-DDTHH:MM:SS; no daily repeated processing."
  type        = string
  default     = null
  validation {
    condition     = var.scheduled_at == null ? true : can(formatdate("YYYY-MM-DD'T'hh:mm:ss", "${var.scheduled_at}Z"))
    error_message = "Supply a valid UTC date/time without a timezone suffix."
  }
}
# enable_schedule: actually arm the schedule. Requires the schedule to exist
# and scheduled_execution_approved = true (explicit approval for a paid run).
variable "enable_schedule" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_schedule || (var.provision_schedule && var.scheduled_execution_approved)
    error_message = "Enabling a paid scheduled run requires explicit separate execution approval."
  }
}
# scheduled_execution_approved: human sign-off; also forwarded in the
# execution input as execution_approved.
variable "scheduled_execution_approved" {
  type    = bool
  default = false
}
