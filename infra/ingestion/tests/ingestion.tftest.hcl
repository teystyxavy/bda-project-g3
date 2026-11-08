mock_provider "aws" {}
run "bounded_and_separate_from_existing_pipeline" {
  command = plan
  assert {
    condition     = aws_glue_job.job["curate"].max_retries == 0 && aws_glue_job.job["score"].number_of_workers == 2 && aws_glue_job.job["score"].timeout == 10
    error_message = "Bound Glue compute and prohibit automatic paid retries."
  }
  assert {
    condition     = aws_lambda_function.control.reserved_concurrent_executions == 1 && aws_glue_catalog_database.runs.name == "is459_g3_ingest"
    error_message = "Serialize publication and isolate the new run catalog."
  }
  assert {
    condition     = !contains(keys(jsondecode(file("${path.module}/workflow.asl.json")).States.Export), "Retry") && jsondecode(file("${path.module}/workflow.asl.json")).States.ScoreAndAnalyze.Type == "Parallel"
    error_message = "Paid jobs must not retry; training and both business answers must be orchestrated."
  }
  assert {
    condition     = length(aws_s3_object.deployment_artifact) == 16 && aws_s3_object.configuration.key == "pipeline/deployment.json"
    error_message = "Only allowlisted artifacts and the deployment pointer may be uploaded."
  }
}

run "glue_reference_and_training_only_sagemaker" {
  command = plan
  assert {
    condition = aws_glue_job.job["baseline"].worker_type == "G.2X" && aws_glue_job.job["export"].timeout == 30 && aws_glue_job.job["baseline"].max_retries == 0
    error_message = "Reference computations need bounded G.2X Glue jobs with no automatic retry."
  }
  assert {
    condition = !strcontains(file("${path.module}/main.tf"), "sagemaker:CreateProcessingJob") && !strcontains(file("${path.module}/main.tf"), "sagemaker:DescribeProcessingJob") && !strcontains(file("${path.module}/workflow.asl.json"), "createProcessingJob")
    error_message = "SageMaker must be limited to training."
  }
}

run "network_replaces_athena_corridors" {
  command = plan
  assert {
    condition     = aws_glue_job.job["network"].worker_type == "G.1X" && endswith(aws_glue_job.job["network"].command[0].script_location, "network_mules.py")
    error_message = "The mule-network job must run network_mules.py on a G.1X Spark worker, like curate/score."
  }
  assert {
    condition     = !strcontains(file("${path.module}/main.tf"), "athena:") && !strcontains(file("${path.module}/workflow.asl.json"), "athena:")
    error_message = "The retired Athena corridor query must not be wired into any IAM policy or the workflow."
  }
}

run "export_runs_the_pinned_improved_runtime" {
  command = plan
  variables {
    export_deployment = { prefix = "pipeline/deployments/export-improved-e31cd65b0440cf1680cd04e6c39db18ca3e7e60865839eb9427b95dbc50c579b" }
  }
  assert {
    condition     = endswith(aws_glue_job.job["export"].command[0].script_location, "/export-improved-e31cd65b0440cf1680cd04e6c39db18ca3e7e60865839eb9427b95dbc50c579b/pipeline_stage.py") && endswith(aws_glue_job.job["export"].non_overridable_arguments["--extra-py-files"], "/export-improved-e31cd65b0440cf1680cd04e6c39db18ca3e7e60865839eb9427b95dbc50c579b/bda_pipeline.zip") && length(regexall("/export-improved-e31cd65b0440cf1680cd04e6c39db18ca3e7e60865839eb9427b95dbc50c579b/dashboard/", aws_glue_job.job["export"].non_overridable_arguments["--extra-files"])) == 3
    error_message = "The export job must run the pinned export script, package and dashboard shell."
  }
  assert {
    condition     = aws_glue_job.job["export"].non_overridable_arguments["--NETWORK_CHECKPOINTS"] == "true" && aws_glue_job.job["export"].non_overridable_arguments["--OVERSIZED_COMPONENT_REVIEW"] == "true" && aws_glue_job.job["export"].non_overridable_arguments["--CHECKPOINT_READ_ONLY"] == "false" && !contains(keys(aws_glue_job.job["export"].non_overridable_arguments), "--RECOVERY_ID")
    error_message = "The canonical export must checkpoint and review oversized components without a recovery identity."
  }
  assert {
    condition     = !contains(keys(aws_glue_job.job["baseline"].non_overridable_arguments), "--NETWORK_CHECKPOINTS") && !strcontains(aws_glue_job.job["baseline"].command[0].script_location, "export-improved-") && length(aws_s3_object.deployment_artifact) == 16
    error_message = "Only the export job changes; baseline and the uploaded artifacts are unaffected."
  }
}
