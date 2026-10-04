output "bucket_name" { value = aws_s3_bucket.pipeline.id }
output "glue_job_name" { value = aws_glue_job.curate.name }
output "athena_workgroup" { value = aws_athena_workgroup.analysis.name }
output "catalog_database" { value = aws_glue_catalog_database.curated.name }
output "log_groups" { value = [for group in aws_cloudwatch_log_group.glue : group.name] }
