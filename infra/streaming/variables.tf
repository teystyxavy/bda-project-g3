variable "aws_account_id" {
  type    = string
  default = "588738581042"
  validation {
    condition     = var.aws_account_id == "588738581042"
    error_message = "Use Group 3's reviewed account."
  }
}
variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
  validation {
    condition     = var.aws_region == "ap-southeast-1"
    error_message = "Use the reviewed Singapore region."
  }
}
variable "provision_streaming" {
  type    = bool
  default = false
}
variable "run_id" {
  type    = string
  default = "stream-smoke-20261006"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,49}$", var.run_id))
    error_message = "Use a fresh safe replay run ID."
  }
}
variable "image_uri" {
  type    = string
  default = null
  validation {
    condition     = var.image_uri == null ? true : can(regex("^588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-streaming@sha256:[0-9a-f]{64}$", var.image_uri))
    error_message = "Pin the verified image digest in this project's ECR repository."
  }
}
variable "enable_consumer" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_consumer || (var.provision_streaming && var.provision_failure_bucket && var.image_uri != null && var.replay_approved)
    error_message = "Consumer activation requires the reviewed image, isolated failure bucket and explicit replay approval."
  }
}
variable "replay_approved" {
  type    = bool
  default = false
}
variable "provision_failure_bucket" {
  type    = bool
  default = false
  validation {
    condition     = !var.provision_failure_bucket || var.provision_streaming
    error_message = "The isolated failure bucket is part of the streaming stack."
  }
}
