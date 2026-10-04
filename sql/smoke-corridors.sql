WITH corridors AS (
    SELECT txn_date, from_bank, to_bank, count(*) AS transactions,
           sum(amount_usd) AS volume_usd
    FROM smoke_transactions
    GROUP BY txn_date, from_bank, to_bank
), shares AS (
    SELECT *, volume_usd / nullif(sum(volume_usd) OVER (PARTITION BY txn_date), 0) AS volume_share
    FROM corridors
)
SELECT *, sum(volume_share * volume_share) OVER (PARTITION BY txn_date) AS daily_hhi
FROM shares
ORDER BY txn_date, volume_usd DESC;
