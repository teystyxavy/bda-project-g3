mock_provider "aws" {}
variables { aws_account_id = "123456789012" }

run "default_creates_nothing" {
  command = plan
  assert {
    condition     = length(aws_glue_job.scoring) == 0 && length(aws_iam_role.scoring) == 0 && length(aws_cloudwatch_log_group.scoring) == 0 && length(aws_cloudwatch_event_rule.failure) == 0
    error_message = "Scoring must provision nothing by default."
  }
}
run "bounded_smoke_and_scoped_permissions" {
  command = plan
  variables { provision_scoring = true }
  assert {
    condition     = aws_glue_job.scoring[0].number_of_workers == 2 && aws_glue_job.scoring[0].timeout == 10 && aws_glue_job.scoring[0].max_retries == 0 && aws_glue_job.scoring[0].execution_property[0].max_concurrent_runs == 1
    error_message = "Keep the reviewed worker, timeout, retry and concurrency limits."
  }
  assert {
    condition     = aws_glue_job.scoring[0].non_overridable_arguments["--EXPECTED_ROWS"] == "1024" && aws_glue_job.scoring[0].non_overridable_arguments["--python-modules-installer-option"] == "--no-index" && length(split(",", aws_glue_job.scoring[0].non_overridable_arguments["--extra-files"])) == 5
    error_message = "Use the checked small cohort and frozen offline artifacts."
  }
  assert {
    condition     = toset(output.runtime_permissions.Statement[3].Resource) == toset(["arn:aws:s3:::is459-g3-123456789012-ap-southeast-1/scored/smoke-20261005/transactions/*", "arn:aws:s3:::is459-g3-123456789012-ap-southeast-1/tmp/scoring/smoke-20261005/*"])
    error_message = "Writes/deletes must be confined to this run's output and temporary prefixes."
  }
  assert {
    condition     = jsondecode(aws_iam_role.scoring[0].assume_role_policy).Statement[0].Condition.ArnEquals["aws:SourceArn"] == "arn:aws:glue:ap-southeast-1:123456789012:job/is459-g3-score-frozen" && jsondecode(aws_iam_role.scoring[0].assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    error_message = "Keep fail-closed account/job trust conditions."
  }
  assert {
    condition     = contains(jsondecode(aws_cloudwatch_event_rule.failure[0].event_pattern).detail.state, "FAILED") && !contains(keys(aws_glue_job.scoring[0].non_overridable_arguments), "--enable-glue-datacatalog")
    error_message = "Notify on failure without adding catalog access."
  }
}
run "reject_input_escape" {
  command = plan
  variables { input_prefix = "curated/../raw" }
  expect_failures = [var.input_prefix]
}
run "exact_emrfs_markers" {
  command = plan
  variables { provision_scoring = true }
  assert {
    condition = toset(output.runtime_permissions.Statement[5].Resource) == toset([
      "arn:aws:s3:::is459-g3-123456789012-ap-southeast-1/scored/smoke-20261005/transactions_$folder$",
      "arn:aws:s3:::is459-g3-123456789012-ap-southeast-1/tmp/scoring/smoke-20261005_$folder$"
    ]) && length(output.runtime_permissions.Statement[1].Condition.StringLike["s3:prefix"]) == 10
    error_message = "Permit only the two exact EMRFS root markers; never broaden to sibling prefixes."
  }
}
run "explicit_success_marker" {
  command = plan
  variables { provision_scoring = true }
  assert {
    condition     = aws_glue_job.scoring[0].non_overridable_arguments["--conf"] == "spark.hadoop.mapreduce.fileoutputcommitter.marksuccessfuljobs=true"
    error_message = "Select direct _SUCCESS creation rather than EMRFS ancestor mkdirs at final commit."
  }
}
run "reject_run_escape" {
  command = plan
  variables { run_id = "../curated" }
  expect_failures = [var.run_id]
}
run "reject_unbounded_rows" {
  command = plan
  variables { expected_rows = 5078346 }
  expect_failures = [var.expected_rows]
}
