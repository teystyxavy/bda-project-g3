# =============================================================================
# infra/scoring/variables.tf -- inputs for the frozen scoring job
# =============================================================================
# aws_account_id: the approved 12-digit account (no default).

variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Supply the approved twelve-digit AWS account ID."
  }
}
# aws_region: deployment region (Singapore).
variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}
# provision_scoring: master switch for this stack.
variable "provision_scoring" {
  type        = bool
  default     = false
  description = "Creates scoring resources only after deployment approval; never starts a run."
}
# run_id: names this run's output (scored/<run_id>/) and temp prefixes.
variable "run_id" {
  type    = string
  default = "smoke-20261005"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,49}$", var.run_id))
    error_message = "Use a fresh safe run ID (3–50 lowercase letters, digits or hyphens)."
  }
}
# input_prefix: which curated dataset to score (default: the 1,024-row scoring
# smoke sample). The regex forbids "..", wildcards and a trailing slash so the
# IAM policy cannot be widened by a crafted value.
variable "input_prefix" {
  type    = string
  default = "curated/scoring-smoke-20261005"
  validation {
    condition     = can(regex("^curated/[a-z0-9-]+(/[a-z0-9-]+)*$", var.input_prefix))
    error_message = "Input must be an explicit curated prefix without traversal, wildcards or trailing slash."
  }
}
# expected_rows: whole number of rows the job must read and write; capped at
# 5,078,345 (the full HI-Small dataset size).
variable "expected_rows" {
  type    = number
  default = 1024
  validation {
    condition     = var.expected_rows > 0 && var.expected_rows <= 5078345 && floor(var.expected_rows) == var.expected_rows
    error_message = "Expected rows must be a positive integer no larger than HI-Small."
  }
}
# artifact_prefix: the immutable scoring-artifacts/<name> folder holding the
# script, package, wheels and model.
variable "artifact_prefix" {
  type    = string
  default = "scoring-artifacts/frozen-20261005"
  validation {
    condition     = can(regex("^scoring-artifacts/[a-z0-9-]+$", var.artifact_prefix))
    error_message = "Use one immutable, explicitly named scoring artifact prefix."
  }
}
# manifest_sha256: SHA-256 of model-manifest.json verified locally; the job
# refuses to score if the uploaded manifest does not match.
variable "manifest_sha256" {
  type    = string
  default = "323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f"
  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.manifest_sha256))
    error_message = "Use the locally verified frozen artifact manifest SHA256."
  }
}
# model_filename: the XGBoost model file (Universal Binary JSON, .ubj).
variable "model_filename" {
  type    = string
  default = "hist-depth5.ubj"
  validation {
    condition     = can(regex("^[a-z0-9-]+\\.ubj$", var.model_filename))
    error_message = "Use a simple UBJ model basename, not a path."
  }
}
