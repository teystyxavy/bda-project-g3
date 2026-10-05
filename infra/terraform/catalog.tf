locals {
  curated_columns = merge(
    { for name in ["from_bank", "from_account", "to_bank", "to_account", "receiving_currency", "payment_currency", "payment_format", "from_bank_name", "from_entity_id", "from_entity_name", "to_bank_name", "to_entity_id", "to_entity_name"] : name => "string" },
    { for name in ["amount_received", "amount_paid", "amount_usd", "amount_usd_asof"] : name => "double" },
    { timestamp = "timestamp", is_laundering = "int", is_self_txn = "boolean", currency_mismatch = "boolean" }
  )
}

resource "aws_glue_catalog_table" "smoke" {
  name          = "smoke_transactions"
  database_name = aws_glue_catalog_database.curated.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    "classification"                    = "parquet"
    "projection.enabled"                = "true"
    "projection.txn_date.type"          = "date"
    "projection.txn_date.range"         = "2022-09-01,2022-09-18"
    "projection.txn_date.format"        = "yyyy-MM-dd"
    "projection.txn_date.interval"      = "1"
    "projection.txn_date.interval.unit" = "DAYS"
    "storage.location.template"         = "s3://${aws_s3_bucket.pipeline.id}/curated/smoke/transactions/txn_date=$${txn_date}/"
  }
  partition_keys {
    name = "txn_date"
    type = "string"
  }
  storage_descriptor {
    location      = "s3://${aws_s3_bucket.pipeline.id}/curated/smoke/transactions/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters            = { "serialization.format" = "1" }
    }
    dynamic "columns" {
      for_each = local.curated_columns
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

resource "aws_athena_named_query" "reconcile" {
  name      = "${var.project_name}-smoke-reconciliation"
  database  = aws_glue_catalog_database.curated.name
  workgroup = aws_athena_workgroup.analysis.name
  query     = file("${path.module}/../../sql/reconcile-smoke.sql")
}

resource "aws_athena_named_query" "corridors" {
  name      = "${var.project_name}-smoke-corridors"
  database  = aws_glue_catalog_database.curated.name
  workgroup = aws_athena_workgroup.analysis.name
  query     = file("${path.module}/../../sql/smoke-corridors.sql")
}

# A separate table keeps the verified smoke results and full run isolated.
resource "aws_glue_catalog_table" "full" {
  name          = "full_transactions"
  database_name = aws_glue_catalog_database.curated.name
  table_type    = "EXTERNAL_TABLE"
  parameters = merge(aws_glue_catalog_table.smoke.parameters, {
    "storage.location.template" = "s3://${aws_s3_bucket.pipeline.id}/curated/full-optimized-20261005/transactions/txn_date=$${txn_date}/"
  })
  partition_keys {
    name = "txn_date"
    type = "string"
  }
  storage_descriptor {
    location      = "s3://${aws_s3_bucket.pipeline.id}/curated/full-optimized-20261005/transactions/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters            = { "serialization.format" = "1" }
    }
    dynamic "columns" {
      for_each = merge(local.curated_columns, { transaction_id = "string" }, {
        for name in ["source_row_index", "txn_count_1h", "txn_count_24h", "txn_count_7d", "distinct_counterparties_24h", "distinct_counterparties_7d"] : name => "bigint"
      })
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}
