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

variable "provision_hosting" {
  description = "Creates a separate hosting stack only after deployment approval. Default creates nothing."
  type        = bool
  default     = false
}

variable "publish_dashboard" {
  description = "Enables public CloudFront delivery. Keep false during staging."
  type        = bool
  default     = false
  validation {
    condition     = !var.publish_dashboard || (var.provision_hosting && var.public_demo_approved)
    error_message = "Publishing requires hosting provisioned and explicit public-demo approval."
  }
}

variable "public_demo_approved" {
  description = "Human approval to expose the reviewed synthetic dashboard and accept traffic costs."
  type        = bool
  default     = false
}
