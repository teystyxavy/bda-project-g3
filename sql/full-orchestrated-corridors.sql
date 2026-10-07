-- =============================================================================
-- sql/full-orchestrated-corridors.sql -- BQ2 corridor volume & concentration
-- =============================================================================
-- Runs in Athena (workgroup is459-g3-analysis, database is459_g3_curated) over
-- the full curated table full_transactions. Embedded into the reviewed batch
-- workflow by infra/orchestration/main.tf (local.values.query_sql) and checked
-- by scripts/validation/verify-orchestrated-corridors.py.
--
-- Output: one row per (corridor_type, txn_date, from_node, to_node) with
--   transactions  -- number of payments in that directed corridor that day;
--   volume_usd    -- total USD value (amount_usd);
--   volume_share  -- that corridor's share of the day's total volume;
--   daily_hhi     -- Herfindahl-Hirschman Index for the day = sum of squared
--                    shares (0..1; close to 1 = volume concentrated in a few
--                    corridors, close to 0 = spread out).
-- This is a treasury/correspondent-banking view, not an AML risk measure.

-- BQ2: directed volume and concentration, never an AML suspiciousness score.
-- Step 1 -- "directed": aggregate volumes for two kinds of corridor and stack
-- them with UNION ALL. Direction matters (A->B is different from B->A).
WITH directed AS (
    -- (a) bank corridors: sending bank -> receiving bank, per day.
    SELECT 'bank' AS corridor_type, txn_date,
           from_bank AS from_node, to_bank AS to_node,
           count(*) AS transactions, sum(amount_usd) AS volume_usd
    FROM full_transactions
    -- Filtering on the partition column txn_date lets Athena read only the 18
    -- daily partitions of the dataset (partition projection), keeping scan cost
    -- low.
    WHERE txn_date BETWEEN '2022-09-01' AND '2022-09-18'
    GROUP BY txn_date, from_bank, to_bank
    UNION ALL
    -- (b) currency corridors: payment currency -> receiving currency, per day.
    SELECT 'currency' AS corridor_type, txn_date,
           payment_currency AS from_node, receiving_currency AS to_node,
           count(*) AS transactions, sum(amount_usd) AS volume_usd
    FROM full_transactions
    WHERE txn_date BETWEEN '2022-09-01' AND '2022-09-18'
    GROUP BY txn_date, payment_currency, receiving_currency
-- Step 2 -- "shares": each corridor's fraction of the day's total volume for
-- its corridor type. The window SUM(...) OVER (PARTITION BY corridor_type,
-- txn_date) is the day's total; NULLIF(..., 0) avoids division by zero.
), shares AS (
    SELECT *, volume_usd / nullif(
        sum(volume_usd) OVER (PARTITION BY corridor_type, txn_date), 0
    ) AS volume_share
    FROM directed
)
-- Step 3 -- HHI: sum of squared shares across all corridors of that type/day,
-- repeated on every row; sorted largest corridors first within each day.
SELECT *, sum(volume_share * volume_share) OVER (
    PARTITION BY corridor_type, txn_date
) AS daily_hhi
FROM shares
ORDER BY corridor_type, txn_date, volume_usd DESC, from_node, to_node;
