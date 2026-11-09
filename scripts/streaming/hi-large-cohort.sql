-- HI-Large streaming cohort (run stream-hilarge-20261109), picked with Athena because the
-- curated HI-Large run (179.7M rows) is too large to scan on a laptop.
-- Source: pipeline/runs/hi-large-delta1-20261107-batch/curated/ through a full-schema external
-- table (is459_g3_ingest.stream_hilarge_curated_full, same location and txn_date projection as
-- run_hi_large_delta1_20261107_batch). Workgroup is459-g3-analysis (8 GB scan cutoff per query).
-- Sender key = streaming.account_key: SHA-256 of the canonical JSON ["<bank without leading zeros>","<account>"].
-- No labels are read.

-- 1. Candidates: senders with 20-40 payments in the whole dataset, all inside the streaming
--    calendar (1 Sep 2022 - 31 Jan 2023, streaming.FIRST_DAY/END_DAY). 36 qualify; the 32
--    lexicographically first keys are the cohort (every receiver has <= 639 payments, under MAX_HISTORY).
WITH t AS (
  SELECT lower(to_hex(sha256(to_utf8('["' || coalesce(nullif(regexp_replace(from_bank, '^0+', ''), ''), '0')
                                     || '","' || from_account || '"]')))) AS sk, timestamp
  FROM is459_g3_ingest.stream_hilarge_curated_full)
SELECT sk, count(*) AS c, min(timestamp) AS first_ts, max(timestamp) AS last_ts
FROM t GROUP BY sk
HAVING count(*) BETWEEN 20 AND 40
   AND min(timestamp) >= timestamp '2022-09-01 00:00:00'
   AND max(timestamp) <  timestamp '2023-02-01 00:00:00'
ORDER BY sk LIMIT 400;

-- 2. Extract (run once per txn_date range to stay under the scan cutoff, then merged into one
--    Parquet file): every row the 32 senders sent, and every row into their 321 receivers.
--    <senders> and <receivers> are the key lists from step 1 and from the senders' rows.
UNLOAD (
  SELECT source_row_index, transaction_id, timestamp, from_bank, from_account, to_bank, to_account,
         amount_received, receiving_currency, amount_paid, payment_currency, payment_format,
         amount_usd, amount_usd_asof, is_self_txn, currency_mismatch,
         txn_count_1h, txn_count_24h, txn_count_7d, distinct_counterparties_24h, distinct_counterparties_7d,
         amount_sum_24h, amount_sum_7d, in_txn_count_1h, in_txn_count_24h, in_txn_count_7d,
         distinct_senders_24h, distinct_senders_7d, in_amount_sum_24h, in_amount_sum_7d, corridor_prior_count
  FROM (SELECT *,
          lower(to_hex(sha256(to_utf8('["' || coalesce(nullif(regexp_replace(from_bank, '^0+', ''), ''), '0') || '","' || from_account || '"]')))) AS sk,
          lower(to_hex(sha256(to_utf8('["' || coalesce(nullif(regexp_replace(to_bank, '^0+', ''), ''), '0') || '","' || to_account || '"]')))) AS rk
        FROM is459_g3_ingest.stream_hilarge_curated_full
        WHERE txn_date BETWEEN '<from>' AND '<to>')
  WHERE sk IN (<senders>) OR rk IN (<receivers>))
TO 's3://is459-g3-588738581042-ap-southeast-1/athena-results/stream-hilarge-20261109/extract/part=<n>/'
WITH (format = 'PARQUET', compression = 'SNAPPY');
-- Result: 37,754 rows (2022-08-01 to 2022-12-31), about USD 0.15 of Athena scans in total.
