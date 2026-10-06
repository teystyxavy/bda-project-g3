mock_provider "aws" {}
run "training_has_no_persistent_compute" {
  command = plan
  assert {
    condition     = aws_ecr_repository.training.image_tag_mutability == "IMMUTABLE" && !aws_ecr_repository.training.force_delete
    error_message = "Training images must be immutable and protected from forced deletion."
  }
  assert {
    condition     = jsondecode(aws_iam_role.training.assume_role_policy).Statement[0].Condition.ArnLike["aws:SourceArn"] == "arn:aws:sagemaker:ap-southeast-1:588738581042:training-job/is459-g3-train-*"
    error_message = "Scope trust to Group 3 training jobs."
  }
}
