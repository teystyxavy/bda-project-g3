mock_provider "aws" {}
variables {
  image_uri = "588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-acquisition@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
run "bounded_cloud_acquisition_reuses_existing_batch" {
  command = plan
  assert {
    condition     = aws_lambda_function.acquisition.timeout == 900 && aws_lambda_function.acquisition.memory_size == 3072 && aws_lambda_function.acquisition.ephemeral_storage[0].size == 10240 && aws_lambda_function.acquisition.reserved_concurrent_executions == 1
    error_message = "Acquisition must stay bounded and serialize its source cache."
  }
  assert {
    condition     = !contains(keys(jsondecode(file("${path.module}/workflow.asl.json")).States.Acquire), "Retry") && !contains(keys(jsondecode(file("${path.module}/workflow.asl.json")).States.Batch), "Retry")
    error_message = "Do not automatically repeat acquisition or paid batch executions."
  }
  assert {
    condition     = local.batch == "arn:aws:states:ap-southeast-1:588738581042:stateMachine:is459-g3-ingest" && aws_ecr_repository.acquisition.image_tag_mutability == "IMMUTABLE"
    error_message = "Reuse the deployed batch and pin an immutable acquisition image."
  }
}
