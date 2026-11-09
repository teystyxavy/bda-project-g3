# =============================================================================
# infra/terraform/catalog.tf -- Glue Data Catalog tables + saved Athena queries
# =============================================================================
# Part of the foundation stack (same Terraform directory as main.tf). It tells
# Athena how to read the curated Parquet written by the Glue curate job:
#   * smoke_transactions -> s3://<bucket>/curated/smoke/transactions/
#   * full_transactions  -> s3://<bucket>/curated/full-optimized-20261005/transactions/
# Both tables use "partition projection": Athena computes the txn_date=...
# partition folders from a date range instead of needing MSCK REPAIR / Glue
# crawlers, so new data is queryable without any extra step or crawler cost.
# It also stores two Athena "named queries" (saved SQL from ../../sql/) that
# reconcile the smoke output and (legacy) sum bank-to-bank volume on it.

locals {
  # Column schema shared by both tables, built as one map {column => Hive type}.
  # This must match the Parquet schema written by jobs/curate_transactions.py:
  #   - string columns: bank/account ids, currencies, payment format and the
  #     entity ids/names joined from the accounts file;
  #   - double columns: original amounts plus amount_usd (converted with the FX
  #     rate) and amount_usd_asof (converted with an as-of FX rate);
  #   - timestamp, the is_laundering label (0/1), and two boolean flags
  #     (is_self_txn: same bank AND same account on both sides; currency_mismatch: payment and
  #     receiving currency differ).
  # merge() combines the three maps into one.
  curated_columns = merge(
    { for name in ["from_bank", "from_account", "to_bank", "to_account", "receiving_currency", "payment_currency", "payment_format", "from_bank_name", "from_entity_id", "from_entity_name", "to_bank_name", "to_entity_id", "to_entity_name"] : name => "string" },
    { for name in ["amount_received", "amount_paid", "amount_usd", "amount_usd_asof"] : name => "double" },
    { timestamp = "timestamp", is_laundering = "int", is_self_txn = "boolean", currency_mismatch = "boolean" }
  )
}

# External table over the smoke-test (1,000-row) curated output. EXTERNAL means
# dropping the table never deletes the S3 data.
resource "aws_glue_catalog_table" "smoke" {
  name          = "smoke_transactions"
  database_name = aws_glue_catalog_database.curated.name
  table_type    = "EXTERNAL_TABLE"
  # Table properties:
  #   classification = parquet (file format hint for Athena/Glue);
  #   projection.* = partition projection on txn_date: a daily date partition
  #     covering 2022-09-01 to 2022-09-18 (the date span of the IBM AML HI-Small
  #     dataset), formatted yyyy-MM-dd;
  #   storage.location.template = where each projected partition lives. "$${...}"
  #     escapes Terraform interpolation so the literal ${txn_date} reaches Athena.
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
  # txn_date is a partition column (folder name), not stored inside the files.
  partition_keys {
    name = "txn_date"
    type = "string"
  }
  # Physical storage: Hive's standard Parquet input/output formats and SerDe.
  # The column list is generated from local.curated_columns via a dynamic block
  # (one "columns" block per map entry).
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

# Saved Athena query: ../../sql/reconcile-smoke.sql -- totals/null checks used
# to prove the smoke output matches the source sample.
resource "aws_athena_named_query" "reconcile" {
  name      = "${var.project_name}-smoke-reconciliation"
  database  = aws_glue_catalog_database.curated.name
  workgroup = aws_athena_workgroup.analysis.name
  query     = file("${path.module}/../../sql/reconcile-smoke.sql")
}

# Saved Athena query: ../../sql/smoke-corridors.sql -- legacy smoke check of
# daily bank-to-bank volume, share and HHI. BQ2 itself is now the Glue
# mule-network job; the resource name is kept to avoid replacing it.
resource "aws_athena_named_query" "corridors" {
  name      = "${var.project_name}-smoke-corridors"
  database  = aws_glue_catalog_database.curated.name
  workgroup = aws_athena_workgroup.analysis.name
  query     = file("${path.module}/../../sql/smoke-corridors.sql")
}

# A separate table keeps the verified smoke results and full run isolated.
# External table over the FULL curated dataset (5,078,345 rows) produced by the
# optimised full run of 5 Oct 2026. It reuses the smoke table's projection
# settings (merge() copies them) and only overrides the S3 location template.
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
    # Full-run columns = the shared curated columns, plus:
    #   transaction_id (string) -- stable id for de-duplication/joins;
    #   source_row_index -- original CSV row number (used to prove completeness);
    #   behavioural features computed by the curate job (--behaviour true):
    #   how many earlier transactions the same sending account made in the
    #   preceding 1h / 24h / 7d, and how many distinct counterparties it paid
    #   in the preceding 24h / 7d (sender/outgoing), plus the mirrored
    #   in_txn_count_* / distinct_senders_* receiver/incoming features
    #   (inputs to the XGBoost AML risk model for BQ1); rolling USD sums over
    #   the 24h / 7d windows (amount_sum_*/in_amount_sum_*, structuring
    #   signal); and corridor_prior_count, a directed-pair novelty count
    #   (0 = first time this sender has paid this receiver).
    dynamic "columns" {
      for_each = merge(local.curated_columns, { transaction_id = "string" }, {
        for name in ["source_row_index", "txn_count_1h", "txn_count_24h", "txn_count_7d",
          "distinct_counterparties_24h", "distinct_counterparties_7d",
          "in_txn_count_1h", "in_txn_count_24h", "in_txn_count_7d",
          "distinct_senders_24h", "distinct_senders_7d",
        "corridor_prior_count"] : name => "bigint"
      }, {
        for name in ["amount_sum_24h", "amount_sum_7d",
        "in_amount_sum_24h", "in_amount_sum_7d"] : name => "double"
      })
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}
