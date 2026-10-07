# =============================================================================
# infra/terraform/outputs.tf -- values printed after `terraform apply`
# =============================================================================
# Used by the helper/validation scripts and by humans to find the resources:
#   bucket_name      -- the pipeline data-lake bucket;
#   glue_job_name    -- the smoke curate job to start;
#   athena_workgroup -- workgroup to run the SQL in sql/;
#   catalog_database -- Glue database holding smoke/full transaction tables;
#   log_groups       -- CloudWatch log groups to inspect Glue output/errors.

output "bucket_name" { value = aws_s3_bucket.pipeline.id }
output "glue_job_name" { value = aws_glue_job.curate.name }
output "athena_workgroup" { value = aws_athena_workgroup.analysis.name }
output "catalog_database" { value = aws_glue_catalog_database.curated.name }
output "log_groups" { value = [for group in aws_cloudwatch_log_group.glue : group.name] }
