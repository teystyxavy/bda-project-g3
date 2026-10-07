# =============================================================================
# infra/hosting/variables.tf -- inputs and safety gates for hosting
# =============================================================================
# aws_account_id: approved 12-digit account (no default).

variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "Supply the approved twelve-digit AWS account ID."
  }
}

# aws_region: region of the dashboard bucket (CloudFront itself is global).
variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
}

# provision_hosting: master switch for the whole hosting stack.
variable "provision_hosting" {
  description = "Creates a separate hosting stack only after deployment approval. Default creates nothing."
  type        = bool
  default     = false
}

# publish_dashboard: turns the CloudFront distribution on (public URL). The
# validation refuses it unless hosting exists AND public_demo_approved.
variable "publish_dashboard" {
  description = "Enables public CloudFront delivery. Keep false during staging."
  type        = bool
  default     = false
  validation {
    condition     = !var.publish_dashboard || (var.provision_hosting && var.public_demo_approved)
    error_message = "Publishing requires hosting provisioned and explicit public-demo approval."
  }
}

# public_demo_approved: explicit human approval for public exposure and the
# (small) CloudFront traffic cost.
variable "public_demo_approved" {
  description = "Human approval to expose the reviewed synthetic dashboard and accept traffic costs."
  type        = bool
  default     = false
}
