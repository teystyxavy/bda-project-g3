-- =============================================================================
-- sql/smoke-corridors.sql -- BQ2 bank corridors on the smoke data
-- =============================================================================
-- Saved in Athena as the named query "<project>-smoke-corridors"
-- (infra/terraform/catalog.tf). The smoke-sized forerunner of
-- full-orchestrated-corridors.sql: bank-to-bank only, per day.
-- Step 1 -- "corridors": count and USD volume per directed bank pair per day.

WITH corridors AS (
    SELECT txn_date, from_bank, to_bank, count(*) AS transactions,
           sum(amount_usd) AS volume_usd
    FROM smoke_transactions
    GROUP BY txn_date, from_bank, to_bank
-- Step 2 -- "shares": corridor volume / that day's total volume (NULLIF guards
-- against division by zero).
), shares AS (
    SELECT *, volume_usd / nullif(sum(volume_usd) OVER (PARTITION BY txn_date), 0) AS volume_share
    FROM corridors
)
-- Step 3 -- daily_hhi = sum of squared shares for the day (Herfindahl-Hirschman
-- concentration index, 0..1); largest corridors listed first.
SELECT *, sum(volume_share * volume_share) OVER (PARTITION BY txn_date) AS daily_hhi
FROM shares
ORDER BY txn_date, volume_usd DESC;
