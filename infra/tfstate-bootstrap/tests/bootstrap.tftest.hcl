mock_provider "aws" {}

variables {
  aws_account_id = "123456789012"
}

run "smoke_plan_controls" {
  command = plan

  assert {
    condition = (
      !aws_s3_bucket.state.force_destroy &&
      aws_s3_bucket_public_access_block.state.block_public_acls &&
      aws_s3_bucket_public_access_block.state.block_public_policy &&
      aws_s3_bucket_public_access_block.state.ignore_public_acls &&
      aws_s3_bucket_public_access_block.state.restrict_public_buckets
    )
    error_message = "The state bucket must stay private and refuse destructive emptying."
  }

  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "Versioning must be enabled so a bad state push can be rolled back."
  }

  assert {
    condition = (
      aws_dynamodb_table.lock.billing_mode == "PAY_PER_REQUEST" &&
      aws_dynamodb_table.lock.hash_key == "LockID"
    )
    error_message = "The lock table must use on-demand billing and the LockID hash key Terraform expects."
  }

  assert {
    condition     = aws_s3_bucket.state.bucket != "is459-g3-123456789012-ap-southeast-1"
    error_message = "The state bucket must not collide with the pipeline data bucket's name pattern."
  }
}

run "reject_invalid_account" {
  command = plan
  variables { aws_account_id = "invalid" }
  expect_failures = [var.aws_account_id]
}

run "reject_invalid_project_name" {
  command = plan
  variables {
    aws_account_id = "123456789012"
    project_name   = "Invalid_Name!"
  }
  expect_failures = [var.project_name]
}
