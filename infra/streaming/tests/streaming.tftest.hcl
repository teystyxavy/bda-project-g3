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
run "publisher_off_by_default" {
  command = plan
  variables { provision_streaming = true }
  assert {
    condition     = length(aws_lambda_function.publisher) == 0 && length(aws_scheduler_schedule.publisher) == 0 && output.publisher_configuration == null
    error_message = "The dashboard publisher is opt-in."
  }
}
run "publisher_reads_scores_and_writes_one_snapshot" {
  # Existing local file supplies a hash for mock plans; no Lambda is deployed.
  command = apply
  variables {
    provision_streaming  = true
    provision_publisher  = true
    lambda_zip_path      = "versions.tf"
    replay_events_sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
  assert {
    condition     = aws_scheduler_schedule.publisher[0].state == "DISABLED" && aws_lambda_function.publisher[0].reserved_concurrent_executions == 1 && aws_lambda_function_event_invoke_config.publisher[0].maximum_retry_attempts == 0
    error_message = "Create the publisher disarmed, with one writer and no queued retries."
  }
  assert {
    condition = alltrue([for action in flatten([for statement in jsondecode(aws_iam_role_policy.publisher[0].policy).Statement : statement.Action]) :
    contains(["dynamodb:Scan", "s3:GetObject", "s3:ListBucket", "s3:PutObject", "cloudwatch:DescribeAlarms", "logs:CreateLogStream", "logs:PutLogEvents"], action)])
    error_message = "The publisher may only read scores, input, audit times and alarm state, and write its snapshot."
  }
  assert {
    condition     = one([for statement in jsondecode(aws_iam_role_policy.publisher[0].policy).Statement : statement.Resource if contains(statement.Action, "s3:PutObject")]) == ["arn:aws:s3:::is459-g3-dashboard-588738581042-ap-southeast-1/dashboard/streaming/stream-smoke-20261006/snapshot.json"]
    error_message = "Write only this run's dashboard snapshot."
  }
  assert {
    condition     = aws_lambda_function.publisher[0].environment[0].variables.CONSUMER_ENABLED == "false" && aws_lambda_function.publisher[0].environment[0].variables.INPUT_SHA256 == var.replay_events_sha256 && output.publisher_configuration.snapshot_url == "/streaming/stream-smoke-20261006/snapshot.json"
    error_message = "Publish the reviewed run's snapshot and report the consumer state."
  }
}
run "publisher_requires_reviewed_events" {
  command = plan
  variables {
    provision_streaming = true
    provision_publisher = true
    lambda_zip_path     = "versions.tf"
  }
  expect_failures = [var.provision_publisher]
}
run "publisher_schedule_needs_fixed_end" {
  command = plan
  variables {
    provision_streaming  = true
    provision_publisher  = true
    lambda_zip_path      = "versions.tf"
    replay_events_sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    enable_publisher     = true
  }
  expect_failures = [var.enable_publisher]
}
run "armed_publisher_stops_at_end_time" {
  command = plan
  variables {
    provision_streaming  = true
    provision_publisher  = true
    lambda_zip_path      = "versions.tf"
    replay_events_sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    enable_publisher     = true
    publisher_end_time   = "2026-11-06T12:00:00"
  }
  assert {
    condition     = aws_scheduler_schedule.publisher[0].state == "ENABLED" && aws_scheduler_schedule.publisher[0].end_date == "2026-11-06T12:00:00Z" && aws_scheduler_schedule.publisher[0].schedule_expression == "rate(1 minute)"
    error_message = "An armed schedule fires once a minute and stops at the authorized end time."
  }
}
run "second_run_uses_its_own_repository_and_the_existing_failure_bucket" {
  command = plan
  variables {
    run_id                      = "stream-delta1-20261107"
    repository_name             = "is459-g3-streaming-delta1"
    use_existing_failure_bucket = true
    provision_streaming         = true
    provision_stream            = true
    model_manifest_sha256       = "0b9ba30d77237c9f71a1799235316d9f6dfba042dc70c1c31546513da0ea4b6a"
    image_uri                   = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-streaming-delta1@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = aws_ecr_repository.runtime[0].name == "is459-g3-streaming-delta1" && length(aws_s3_bucket.failures) == 0 && aws_lambda_event_source_mapping.replay[0].destination_config[0].on_failure[0].destination_arn == "arn:aws:s3:::is459-g3-stream-failures-588738581042-ap-southeast-1"
    error_message = "A second run keeps its own repository and reuses the isolated failure bucket without managing it."
  }
  assert {
    condition     = aws_lambda_function.consumer[0].environment[0].variables.MANIFEST_SHA256 == "0b9ba30d77237c9f71a1799235316d9f6dfba042dc70c1c31546513da0ea4b6a" && !aws_lambda_event_source_mapping.replay[0].enabled
    error_message = "The consumer pins the served model manifest and starts disabled."
  }
}
run "image_must_come_from_the_runs_repository" {
  command = plan
  variables {
    repository_name     = "is459-g3-streaming-delta1"
    provision_streaming = true
    image_uri           = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-streaming@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  expect_failures = [var.image_uri]
}
run "failure_bucket_is_created_or_reused_not_both" {
  command = plan
  variables {
    provision_streaming         = true
    provision_failure_bucket    = true
    use_existing_failure_bucket = true
  }
  expect_failures = [var.use_existing_failure_bucket]
}
