variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Supply the approved twelve-digit AWS account ID."
  }
}
variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}
variable "provision_scoring" {
  type        = bool
  default     = false
  description = "Creates scoring resources only after deployment approval; never starts a run."
}
variable "run_id" {
  type    = string
  default = "smoke-20261005"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,49}$", var.run_id))
    error_message = "Use a fresh safe run ID (3–50 lowercase letters, digits or hyphens)."
  }
}
variable "input_prefix" {
  type    = string
  default = "curated/scoring-smoke-20261005"
  validation {
    condition     = can(regex("^curated/[a-z0-9-]+(/[a-z0-9-]+)*$", var.input_prefix))
    error_message = "Input must be an explicit curated prefix without traversal, wildcards or trailing slash."
  }
}
variable "expected_rows" {
  type    = number
  default = 1024
  validation {
    condition     = var.expected_rows > 0 && var.expected_rows <= 5078345 && floor(var.expected_rows) == var.expected_rows
    error_message = "Expected rows must be a positive integer no larger than HI-Small."
  }
}
variable "artifact_prefix" {
  type    = string
  default = "scoring-artifacts/frozen-20261005"
  validation {
    condition     = can(regex("^scoring-artifacts/[a-z0-9-]+$", var.artifact_prefix))
    error_message = "Use one immutable, explicitly named scoring artifact prefix."
  }
}
variable "manifest_sha256" {
  type    = string
  default = "323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f"
  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.manifest_sha256))
    error_message = "Use the locally verified frozen artifact manifest SHA256."
  }
}
variable "model_filename" {
  type    = string
  default = "hist-depth5.ubj"
  validation {
    condition     = can(regex("^[a-z0-9-]+\\.ubj$", var.model_filename))
    error_message = "Use a simple UBJ model basename, not a path."
  }
}
