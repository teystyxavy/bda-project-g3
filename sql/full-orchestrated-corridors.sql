-- BQ2: directed volume and concentration, never an AML suspiciousness score.
WITH directed AS (
    SELECT 'bank' AS corridor_type, txn_date,
           from_bank AS from_node, to_bank AS to_node,
           count(*) AS transactions, sum(amount_usd) AS volume_usd
    FROM full_transactions
    WHERE txn_date BETWEEN '2022-09-01' AND '2022-09-18'
    GROUP BY txn_date, from_bank, to_bank
    UNION ALL
    SELECT 'currency' AS corridor_type, txn_date,
           payment_currency AS from_node, receiving_currency AS to_node,
           count(*) AS transactions, sum(amount_usd) AS volume_usd
    FROM full_transactions
    WHERE txn_date BETWEEN '2022-09-01' AND '2022-09-18'
    GROUP BY txn_date, payment_currency, receiving_currency
), shares AS (
    SELECT *, volume_usd / nullif(
        sum(volume_usd) OVER (PARTITION BY corridor_type, txn_date), 0
    ) AS volume_share
    FROM directed
)
SELECT *, sum(volume_share * volume_share) OVER (
    PARTITION BY corridor_type, txn_date
) AS daily_hhi
FROM shares
ORDER BY corridor_type, txn_date, volume_usd DESC, from_node, to_node;
