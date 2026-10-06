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
    condition     = length(aws_s3_object.deployment_artifact) == 11 && aws_s3_object.configuration.key == "pipeline/deployment.json"
    error_message = "Only allowlisted artifacts and the deployment pointer may be uploaded."
  }
}
