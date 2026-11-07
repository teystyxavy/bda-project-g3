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
    condition     = var.image_uri == null ? true : can(regex("^588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/${var.repository_name}@sha256:[0-9a-f]{64}$", var.image_uri))
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
    condition     = !var.enable_consumer || (var.provision_streaming && (var.provision_failure_bucket || var.use_existing_failure_bucket) && var.provision_stream && var.image_uri != null && var.replay_approved)
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
# provision_publisher: create the dashboard publisher (Lambda, disabled
# schedule, alarm). It reads committed scores and writes one public snapshot;
# it cannot score, replay or change state. Needs the packaged zip and the
# reviewed replay events' SHA-256, which the publisher checks before use.
variable "provision_publisher" {
  type    = bool
  default = false
  validation {
    condition     = !var.provision_publisher || (var.provision_streaming && var.lambda_zip_path != null && var.replay_events_sha256 != null)
    error_message = "The publisher needs the streaming stack, its packaged zip and the reviewed replay events digest."
  }
}
# lambda_zip_path: the publisher's zip, from scripts/aws/prepare-dashboard-streaming.py.
# Named like dashboard-status's input so scripts/aws/get-terraform-inputs.ps1 can
# fill it with the deployed package on another machine.
variable "lambda_zip_path" {
  type    = string
  default = null
}
# replay_events_sha256: events_sha256 from the replay package's
# verification.json; the same file must be uploaded to
# s3://<pipeline bucket>/streaming/<run_id>/input/events.jsonl.
variable "replay_events_sha256" {
  type    = string
  default = null
  validation {
    condition     = var.replay_events_sha256 == null ? true : can(regex("^[0-9a-f]{64}$", var.replay_events_sha256))
    error_message = "Use the events_sha256 recorded in the reviewed replay package."
  }
}
# enable_publisher: arm the once-a-minute schedule. Only for an authorized
# demo or recording window, so a fixed end time is required.
variable "enable_publisher" {
  type    = bool
  default = false
  validation {
    condition     = !var.enable_publisher || (var.provision_publisher && var.publisher_end_time != null)
    error_message = "Enabling the publisher schedule requires the publisher and a fixed UTC end time."
  }
}
# publisher_end_time: UTC time YYYY-MM-DDTHH:MM:SS after which the schedule
# stops firing, even if nobody disables it.
variable "publisher_end_time" {
  type    = string
  default = null
  validation {
    condition     = var.publisher_end_time == null ? true : can(formatdate("YYYY-MM-DD'T'hh:mm:ss", "${var.publisher_end_time}Z"))
    error_message = "Supply a valid UTC date/time without a timezone suffix."
  }
}
# repository_name: the consumer image's ECR repository, created by this stack.
# A later run kept in its own state uses its own repository, so its repository
# policy (pull rights for that run's function only) cannot replace the first's.
variable "repository_name" {
  type    = string
  default = "is459-g3-streaming"
  validation {
    condition     = can(regex("^is459-g3-streaming(-[a-z0-9-]{1,40})?$", var.repository_name))
    error_message = "Use the project's streaming repository name."
  }
}
# use_existing_failure_bucket: route failed records to the isolated failure
# bucket another streaming state already manages (same name), instead of
# creating it. Failure records are kept under each mapping's own UUID prefix.
variable "use_existing_failure_bucket" {
  type    = bool
  default = false
  validation {
    condition     = !(var.use_existing_failure_bucket && var.provision_failure_bucket)
    error_message = "Either create the failure bucket or reuse the existing one, not both."
  }
}
# model_manifest_sha256: SHA-256 of the frozen model manifest baked into the
# consumer image. The consumer refuses any other model; the publisher selects
# the model's feature set and batch review cutoff from it.
variable "model_manifest_sha256" {
  type    = string
  default = "323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f"
  validation {
    condition     = can(regex("^[0-9a-f]{64}$", var.model_manifest_sha256))
    error_message = "Use the reviewed model manifest SHA-256."
  }
}
