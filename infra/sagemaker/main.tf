locals {
  account = "588738581042"
  region  = "ap-southeast-1"
  bucket  = "is459-g3-${local.account}-${local.region}"
  jobs    = "arn:aws:sagemaker:${local.region}:${local.account}:training-job/is459-g3-train-*"
  logs    = "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/sagemaker/TrainingJobs"
}
resource "aws_ecr_repository" "training" {
  name                 = "is459-g3-training"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  encryption_configuration { encryption_type = "AES256" }
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_iam_role" "training" {
  name = "is459-g3-sagemaker-training"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = { Service = "sagemaker.amazonaws.com" }, Action = "sts:AssumeRole",
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account },
        ArnLike      = { "aws:SourceArn" = local.jobs }
      }
    }]
  })
}
resource "aws_iam_role_policy" "training" {
  name = "is459-g3-training-runtime"
  role = aws_iam_role.training.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "BucketMetadata", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = "arn:aws:s3:::${local.bucket}" },
    { Sid = "ListTrainingInputs", Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${local.bucket}",
    Condition = { StringLike = { "s3:prefix" = ["sagemaker/inputs/is459-g3-train-*", "sagemaker/outputs/is459-g3-train-*"] } } },
    { Sid = "ReadTrainingInputs", Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${local.bucket}/sagemaker/inputs/is459-g3-train-*/*" },
    { Sid = "WriteCandidates", Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = "arn:aws:s3:::${local.bucket}/sagemaker/outputs/is459-g3-train-*/*" },
    { Sid = "PullTrainingImage", Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.training.arn },
    { Sid = "EcrLogin", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Sid = "CreateTrainingLogGroup", Effect = "Allow", Action = ["logs:CreateLogGroup"], Resource = "${local.logs}:*" },
    { Sid = "DescribeTrainingLogs", Effect = "Allow", Action = ["logs:DescribeLogStreams"], Resource = "${local.logs}:*" },
    { Sid = "TrainingMetrics", Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*",
    Condition = { StringEquals = { "cloudwatch:namespace" = ["AWS/SageMaker", "/aws/sagemaker/TrainingJobs"] } } },
    { Sid = "TrainingLogs", Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${local.logs}:log-stream:is459-g3-train-*" }
  ] })
}
output "training_role_arn" { value = aws_iam_role.training.arn }
output "training_repository_url" { value = aws_ecr_repository.training.repository_url }
# Attach only through a reviewed identity-policy change. This output changes no caller permissions.
output "launcher_policy_json" {
  value = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["sagemaker:CreateTrainingJob", "sagemaker:DescribeTrainingJob", "sagemaker:StopTrainingJob", "sagemaker:AddTags"], Resource = local.jobs },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.training.arn,
    Condition = { StringEquals = { "iam:PassedToService" = "sagemaker.amazonaws.com" } } },
    { Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = "arn:aws:s3:::${local.bucket}/sagemaker/inputs/is459-g3-train-*/*" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${local.bucket}/sagemaker/outputs/is459-g3-train-*/*" }
  ] })
}
