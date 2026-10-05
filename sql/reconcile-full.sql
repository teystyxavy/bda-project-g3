SELECT count(*) AS transactions,
       count(DISTINCT transaction_id) AS unique_transactions,
       min(source_row_index) AS first_source_row,
       max(source_row_index) AS last_source_row,
       sum(amount_usd) AS amount_usd,
       sum(amount_usd_asof) AS amount_usd_asof,
       sum(is_laundering) AS positives,
       count_if(from_entity_id IS NULL OR to_entity_id IS NULL) AS missing_accounts,
       count_if(amount_usd IS NULL OR amount_usd_asof IS NULL) AS missing_fx,
       count_if(txn_count_1h IS NULL OR txn_count_24h IS NULL OR txn_count_7d IS NULL
                OR distinct_counterparties_24h IS NULL OR distinct_counterparties_7d IS NULL) AS missing_behaviour
FROM full_transactions;
