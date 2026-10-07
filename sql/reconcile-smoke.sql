-- =============================================================================
-- sql/reconcile-smoke.sql -- quality checks on the 1,000-row smoke output
-- =============================================================================
-- Saved in Athena as the named query "<project>-smoke-reconciliation"
-- (infra/terraform/catalog.tf). Returns row count, USD control totals, number
-- of laundering labels and counts of rows whose account join (missing entity
-- ids) or FX conversion failed -- both of which should be 0.

SELECT count(*) AS transactions,
       sum(amount_usd) AS amount_usd,
       sum(amount_usd_asof) AS amount_usd_asof,
       sum(is_laundering) AS positives,
       count_if(from_entity_id IS NULL OR to_entity_id IS NULL) AS missing_accounts,
       count_if(amount_usd IS NULL OR amount_usd_asof IS NULL) AS missing_fx
FROM smoke_transactions;
