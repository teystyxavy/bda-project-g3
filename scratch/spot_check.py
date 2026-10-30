import hashlib
import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from bda_pipeline.analytics import add_behaviour, bank_key
from bda_pipeline.batch import COLUMNS, FEATURES

dataset = ds.dataset("output/curated-full/curated", format="parquet", partitioning="hive")

# Replicate verify_curated's exact sender-sampling logic (ingest_compute.py).
histories = []
for batch in dataset.to_batches(batch_size=100_000):
    frame = batch.to_pandas()
    bank = frame.from_bank.astype(str).str.lstrip('0').replace('', '0')
    keep = pd.Series([int(hashlib.sha256(f'{b}:{a}'.encode()).hexdigest()[:8], 16) % 100 == 0
                      for b, a in zip(bank, frame.from_account)], index=frame.index)
    if keep.any():
        histories.append(frame.loc[keep, COLUMNS])

reference = pd.concat(histories, ignore_index=True).sort_values('source_row_index').reset_index(drop=True)
actual = reference[FEATURES].copy()
recalculated = add_behaviour(reference)

mismatch = (actual.to_numpy() != recalculated[FEATURES].to_numpy())
mismatch_rows = np.where(mismatch.any(axis=1))[0]
print(f"Total mismatched rows in 1% sample: {len(mismatch_rows)} / {len(reference)}")

idx = mismatch_rows[0]
row = reference.iloc[idx]
print("\n--- Row under investigation ---")
print(row[['source_row_index', 'transaction_id', 'timestamp', 'from_bank', 'from_account', 'to_bank', 'to_account']])
print("ACTUAL (Glue, from full-population curation):", actual.iloc[idx].to_dict())
print("LOCAL 1%-SAMPLE RECALC (known incomplete for in_*):", recalculated[FEATURES].iloc[idx].to_dict())

# Ground truth: every transaction in the full dataset paying this row's receiver, regardless of sender.
target_bank = bank_key(pd.Series([row.to_bank])).iloc[0]
target_account = row.to_account
print(f"\nTarget receiver: bank={target_bank!r} account={target_account!r}")

receiver_rows = []
for batch in dataset.to_batches(batch_size=200_000):
    frame = batch.to_pandas()
    tb = bank_key(frame.to_bank)
    match = (tb == target_bank) & (frame.to_account == target_account)
    if match.any():
        receiver_rows.append(frame.loc[match, COLUMNS])

full_receiver_history = pd.concat(receiver_rows, ignore_index=True).sort_values(
    ['timestamp', 'source_row_index']).reset_index(drop=True)
print(f"Full incoming history for this receiver: {len(full_receiver_history)} rows")

recomputed_full = add_behaviour(full_receiver_history)
target = recomputed_full.loc[recomputed_full.source_row_index == row.source_row_index]
in_features = ['in_txn_count_1h', 'in_txn_count_24h', 'in_txn_count_7d', 'distinct_senders_24h', 'distinct_senders_7d']
print("\nGROUND TRUTH (full receiver history, independently recomputed):")
print(target[in_features].to_dict('records')[0])
print("\nGlue's ACTUAL value for the same columns:")
print({k: v for k, v in actual.iloc[idx].to_dict().items() if k in in_features})
