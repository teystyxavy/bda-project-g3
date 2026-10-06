# SageMaker training foundation

This separate Terraform root provisions the ECR repository, training execution
role and scoped runtime policy only. The Python launcher creates bounded jobs
on demand. See [the implementation and runbook](../../sagemaker/README.md).

Validated locally with the AWS 6.67.0 provider and a mocked provider test; local
validation is not an AWS deployment plan. State and real deployment plans must
remain under `%LOCALAPPDATA%/IS459-G3/terraform-sagemaker`, outside OneDrive.

For offline checks, set `TF_DATA_DIR` to an ignored local output directory, run
`terraform -chdir=infra/sagemaker init -backend=false`, then `validate` and `test`.
Tests use `mock_provider "aws"` and never create cloud resources.
