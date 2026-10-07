# =============================================================================
# infra/streaming/variables.tf -- switches and inputs for the streaming demo
# =============================================================================
# Several safety gates are chained: each validation block makes Terraform
# refuse a plan where a more expensive or riskier option is turned on without
# the cheaper prerequisites (and explicit approval).
#
# aws_account_id: locked to Group 3's reviewed account.

variable "aws_account_id" {
  type    = string
  default = "588738581042"
  validation {
    condition     = var.aws_account_id == "588738581042"
    error_message = "Use Group 3's reviewed account."
  }
}
# aws_region: locked to Singapore (ap-southeast-1), where the rest is deployed.
variable "aws_region" {
  type    = string
  default = "ap-southeast-1"
  validation {
    condition     = var.aws_region == "ap-southeast-1"
    error_message = "Use the reviewed Singapore region."
  }
}
# provision_streaming: master switch; false = create nothing at all.
variable "provision_streaming" {
  type    = bool
  default = false
}
# run_id: names all per-run resources (stream, table, function, metric
# namespace). Lowercase/digits/hyphens, 3-50 chars, so it is safe in every
# AWS name it is used in.
variable "run_id" {
  type    = string
  default = "stream-smoke-20261006"
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{2,49}$", var.run_id))
    error_message = "Use a fresh safe replay run ID."
  }
}
# image_uri: the consumer image, which must be this project's ECR repository
# pinned by an exact sha256 digest (not a mutable tag) so the reviewed image is
# exactly what runs. null = no Lambda function is created yet.
variable "image_uri" {
  type    = string
  default = null
  validation {
    condition     = var.image_uri == null ? true : can(regex("^588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-streaming@sha256:[0-9a-f]{64}$", var.image_uri))
    error_message = "Pin the verified image digest in this project's ECR repository."
  }
}
# Parked by default: keeps the image, function, state table and audit, but no
# billable Kinesis shard or consumer mapping. Set true to restore the stream.
variable "provision_stream" {
  type    = bool
  default = false
}
# enable_consumer: turns on the Kinesis -> Lambda mapping (actual processing).
# Only allowed when every prerequisite exists and replay_approved is true.
variable "enable_consumer" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_consumer || (var.provision_streaming && var.provision_failure_bucket && var.provision_stream && var.image_uri != null && var.replay_approved)
    error_message = "Consumer activation requires the reviewed image, isolated failure bucket and explicit replay approval."
  }
}
# replay_approved: explicit human sign-off that a (paid) replay may run.
variable "replay_approved" {
  type    = bool
  default = false
}
# provision_failure_bucket: create the isolated on-failure S3 bucket; only
# valid as part of the streaming stack.
variable "provision_failure_bucket" {
  type    = bool
  default = false
  validation {
    condition     = !var.provision_failure_bucket || var.provision_streaming
    error_message = "The isolated failure bucket is part of the streaming stack."
  }
}
