mock_provider "aws" {
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::588738581042:role/mock-stream-role" } }
  mock_resource "aws_kinesis_stream" { defaults = { arn = "arn:aws:kinesis:ap-southeast-1:588738581042:stream/is459-g3-stream-smoke-20261006" } }
  mock_resource "aws_lambda_function" { defaults = { arn = "arn:aws:lambda:ap-southeast-1:588738581042:function:is459-g3-stream-smoke-20261006" } }
  mock_resource "aws_s3_bucket" { defaults = { arn = "arn:aws:s3:::is459-g3-stream-failures-588738581042-ap-southeast-1" } }
}
run "default_off" {
  command = plan
  assert {
    condition     = length(aws_kinesis_stream.replay) == 0 && length(aws_lambda_function.consumer) == 0
    error_message = "Default must create no streaming resources."
  }
}
run "staging_without_image" {
  command = apply
  variables {
    provision_streaming = true
    provision_stream    = true
  }
  assert {
    condition     = aws_kinesis_stream.replay[0].shard_count == 1 && aws_kinesis_stream.replay[0].kms_key_id == "alias/aws/kinesis" && aws_dynamodb_table.state[0].deletion_protection_enabled && !aws_ecr_repository.runtime[0].force_delete && length(aws_lambda_function.consumer) == 0
    error_message = "Use one encrypted shard, protected state/artifacts and no function before an image is reviewed."
  }
}
run "image_stays_disabled" {
  command = apply
  variables {
    provision_streaming      = true
    provision_failure_bucket = true
    provision_stream         = true
    image_uri                = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-streaming@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.failures[0].block_public_acls && aws_s3_bucket_public_access_block.failures[0].block_public_policy && aws_s3_bucket_public_access_block.failures[0].ignore_public_acls && aws_s3_bucket_public_access_block.failures[0].restrict_public_buckets && !aws_s3_bucket.failures[0].force_destroy && aws_lambda_event_source_mapping.replay[0].destination_config[0].on_failure[0].destination_arn == aws_s3_bucket.failures[0].arn
    error_message = "Failures must use the isolated, private, retained bucket."
  }
  assert {
    condition     = !aws_lambda_event_source_mapping.replay[0].enabled && aws_lambda_event_source_mapping.replay[0].parallelization_factor == 1 && aws_lambda_function.consumer[0].reserved_concurrent_executions == 1 && aws_lambda_event_source_mapping.replay[0].maximum_retry_attempts == 1 && !aws_lambda_event_source_mapping.replay[0].bisect_batch_on_function_error
    error_message = "Preserve ordering, bounded retry and explicit activation."
  }
  assert {
    condition     = toset(aws_lambda_event_source_mapping.replay[0].function_response_types) == toset(["ReportBatchItemFailures"]) && aws_lambda_function.consumer[0].timeout == 60 && aws_lambda_function.consumer[0].memory_size == 2048
    error_message = "Use checked partial-failure protocol and bounded execution."
  }
}
run "parked_keeps_consumer_without_stream" {
  command = plan
  variables {
    provision_streaming      = true
    provision_failure_bucket = true
    image_uri                = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-streaming@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = length(aws_kinesis_stream.replay) == 0 && length(aws_lambda_event_source_mapping.replay) == 0 && length(aws_lambda_function.consumer) == 1 && length(aws_dynamodb_table.state) == 1 && length(aws_ecr_repository.runtime) == 1
    error_message = "Parking must remove only the billable shard and its mapping."
  }
}
run "image_without_failure_bucket_has_no_mapping" {
  command = plan
  variables {
    provision_streaming = true
    image_uri           = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-streaming@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = length(aws_lambda_event_source_mapping.replay) == 0 && length(aws_s3_bucket.failures) == 0
    error_message = "An unapproved failure bucket must create no bucket or consumer mapping."
  }
}
run "reject_unapproved_activation" {
  command = plan
  variables {
    provision_streaming = true
    image_uri           = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-streaming@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    enable_consumer     = true
  }
  expect_failures = [var.enable_consumer]
}
run "reject_external_or_mutable_image" {
  command = plan
  variables { image_uri = "another-repository:latest" }
  expect_failures = [var.image_uri]
}
