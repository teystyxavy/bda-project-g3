# =============================================================================
# infra/sagemaker/main.tf -- SageMaker training support (BQ1 model retraining)
# =============================================================================
# What it is: the long-lived pieces SageMaker needs to (re)train the XGBoost
# AML risk model used for the BQ1 review queue:
#   * a private ECR repository for the training container (sagemaker/Dockerfile,
#     entry point sagemaker/train.py);
#   * an IAM execution role that training jobs named is459-g3-train-* run as.
# Training jobs themselves are NOT created here: they are launched by the
# ingestion Step Functions workflow (infra/ingestion) when a run asks to train,
# or manually via scripts/modeling/sagemaker-training.py. Inputs are read from
# s3://<bucket>/sagemaker/inputs/<job>/ and the candidate model is written to
# s3://<bucket>/sagemaker/outputs/<job>/. Candidates are never auto-promoted.

locals {
  # Fixed account/region/bucket (this stack has no variables), plus:
  #   jobs -- ARN pattern of the only training jobs this role may serve;
  #   logs -- the SageMaker training log group.
  account = "588738581042"
  region  = "ap-southeast-1"
  bucket  = "is459-g3-${local.account}-${local.region}"
  jobs    = "arn:aws:sagemaker:${local.region}:${local.account}:training-job/is459-g3-train-*"
  logs    = "arn:aws:logs:${local.region}:${local.account}:log-group:/aws/sagemaker/TrainingJobs"
}
# ECR repository for the training image: immutable tags, scan on push, AES256.
resource "aws_ecr_repository" "training" {
  name                 = "is459-g3-training"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  encryption_configuration { encryption_type = "AES256" }
  image_scanning_configuration { scan_on_push = true }
}
# Execution role assumed by SageMaker while a training job runs. The trust
# conditions restrict it to SageMaker acting for THIS account and for jobs
# named is459-g3-train-* (confused-deputy protection).
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
# Runtime permissions of a training job, one statement per need:
resource "aws_iam_role_policy" "training" {
  name = "is459-g3-training-runtime"
  role = aws_iam_role.training.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    # BucketMetadata: SageMaker looks up the bucket region before downloading.
    { Sid = "BucketMetadata", Effect = "Allow", Action = ["s3:GetBucketLocation"], Resource = "arn:aws:s3:::${local.bucket}" },
    # ListTrainingInputs: list keys, but only under this project's training input
    # and output prefixes (the s3:prefix condition).
    { Sid = "ListTrainingInputs", Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${local.bucket}",
    Condition = { StringLike = { "s3:prefix" = ["sagemaker/inputs/is459-g3-train-*", "sagemaker/outputs/is459-g3-train-*"] } } },
    # ReadTrainingInputs: download the input channels (baseline scores, manifests).
    # WriteCandidates: upload the trained model artefact (model.tar.gz).
    # PullTrainingImage: pull the training container layers from our ECR repo.
    # EcrLogin: obtain a Docker login token (this API has no resource-level scope,
    #   hence "*").
    { Sid = "ReadTrainingInputs", Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${local.bucket}/sagemaker/inputs/is459-g3-train-*/*" },
    { Sid = "WriteCandidates", Effect = "Allow", Action = ["s3:PutObject", "s3:AbortMultipartUpload"], Resource = "arn:aws:s3:::${local.bucket}/sagemaker/outputs/is459-g3-train-*/*" },
    { Sid = "PullTrainingImage", Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = aws_ecr_repository.training.arn },
    { Sid = "EcrLogin", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    # Create/describe the training log group/streams in CloudWatch Logs.
    { Sid = "CreateTrainingLogGroup", Effect = "Allow", Action = ["logs:CreateLogGroup"], Resource = "${local.logs}:*" },
    { Sid = "DescribeTrainingLogs", Effect = "Allow", Action = ["logs:DescribeLogStreams"], Resource = "${local.logs}:*" },
    # TrainingMetrics: publish training metrics, limited to SageMaker namespaces.
    # TrainingLogs: write log events only to is459-g3-train-* log streams.
    { Sid = "TrainingMetrics", Effect = "Allow", Action = ["cloudwatch:PutMetricData"], Resource = "*",
    Condition = { StringEquals = { "cloudwatch:namespace" = ["AWS/SageMaker", "/aws/sagemaker/TrainingJobs"] } } },
    { Sid = "TrainingLogs", Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${local.logs}:log-stream:is459-g3-train-*" }
  ] })
}
# Outputs: the role ARN to pass as RoleArn and the ECR URL to push images to.
output "training_role_arn" { value = aws_iam_role.training.arn }
output "training_repository_url" { value = aws_ecr_repository.training.repository_url }
# launcher_policy_json: the permissions a HUMAN or CI identity would need to
# launch training jobs (create/describe/stop/tag the job, pass the role only to
# SageMaker, upload inputs and read outputs). It is printed for review only;
# Terraform does not attach it to anyone.
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
