mock_provider "aws" {}

variables {
  aws_account_id = "123456789012"
  alert_email    = "test@example.com"
}

run "smoke_plan_controls" {
  command = plan

  assert {
    condition = (
      !aws_s3_bucket.pipeline.force_destroy &&
      aws_s3_bucket_public_access_block.pipeline.block_public_acls &&
      aws_s3_bucket_public_access_block.pipeline.block_public_policy &&
      aws_s3_bucket_public_access_block.pipeline.ignore_public_acls &&
      aws_s3_bucket_public_access_block.pipeline.restrict_public_buckets
    )
    error_message = "The data bucket must remain private and refuse destructive emptying."
  }

  assert {
    condition = (
      aws_glue_job.curate.number_of_workers == 2 &&
      aws_glue_job.curate.timeout <= 10 &&
      aws_glue_job.curate.max_retries == 0 &&
      aws_glue_job.curate.execution_property[0].max_concurrent_runs == 1 &&
      aws_glue_job.curate.default_arguments["--expected_rows"] == "1000"
    )
    error_message = "The default job must remain bounded to the smoke-run cost and size limits."
  }

  assert {
    condition = (
      aws_athena_workgroup.analysis.configuration[0].enforce_workgroup_configuration &&
      aws_athena_workgroup.analysis.configuration[0].bytes_scanned_cutoff_per_query <= 1073741824 &&
      aws_glue_catalog_table.smoke.parameters["projection.enabled"] == "true" &&
      aws_glue_catalog_table.smoke.partition_keys[0].name == "txn_date"
    )
    error_message = "Athena must use the scan limit and date-partitioned sample catalog."
  }

  assert {
    condition     = length(aws_budgets_budget.account.notification) == 3 && tonumber(aws_budgets_budget.account.limit_amount) == 10
    error_message = "Default budget notices must use the requested USD 10 threshold."
  }
}

run "reject_over_budget" {
  command = plan
  variables { monthly_budget_usd = 11 }
  expect_failures = [var.monthly_budget_usd]
}

run "reject_invalid_account" {
  command = plan
  variables { aws_account_id = "invalid" }
  expect_failures = [var.aws_account_id]
}
