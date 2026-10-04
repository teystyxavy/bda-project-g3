SELECT count(*) AS transactions,
       sum(amount_usd) AS amount_usd,
       sum(amount_usd_asof) AS amount_usd_asof,
       sum(is_laundering) AS positives,
       count_if(from_entity_id IS NULL OR to_entity_id IS NULL) AS missing_accounts,
       count_if(amount_usd IS NULL OR amount_usd_asof IS NULL) AS missing_fx
FROM smoke_transactions;
