# =============================================================================
# infra/tfstate-bootstrap/outputs.tf -- values the other stacks' init needs
# =============================================================================
# Print these once after apply and reuse them as -backend-config values for
# every other stack, e.g.:
#
#   terraform -chdir=infra/ingestion init -migrate-state `
#     -backend-config="bucket=$(bucket_name)" `
#     -backend-config="key=ingestion/terraform.tfstate" `
#     -backend-config="region=$(aws_region)" `
#     -backend-config="dynamodb_table=$(dynamodb_table_name)"
#
# Use a different "key" per stack (foundation/, ingestion/, scoring/, ...);
# bucket, region and dynamodb_table stay the same for all of them.

output "bucket_name" { value = aws_s3_bucket.state.id }
output "dynamodb_table_name" { value = aws_dynamodb_table.lock.name }
output "aws_region" { value = var.aws_region }
