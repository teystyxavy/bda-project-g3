"""Renumbers source_row_index/transaction_id to a dense 0..N-1 range for a date-filtered HI-Large subset.

Why this is needed
-------------------
export_verified_batch (src/bda_pipeline/batch.py) checks that every row's
source_row_index falls in range [0, proof['rows']) and that every index in
that range is seen exactly once - a completeness guarantee that only holds
for a FULL-population export. A subset built by picking a handful of
txn_date partitions out of a full curated run keeps each row's *original*
source_row_index (its position in the full raw CSV), so those indices are
sparse relative to the subset's own row count and the check fails.

This script leaves every column untouched except source_row_index and
transaction_id, which it renumbers 0..N-1 (ordered by the original index,
so within-day and cross-day order is preserved) so the subset can be fed
to export_verified_batch as if it were its own self-contained population.

Usage
-----
    python scripts/local/remap-subset-indices.py \\
        --curated /path/to/curated-subset --output /path/to/remapped --subset HI-Large
"""
import argparse
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--curated', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing directories are rejected')
    parser.add_argument('--subset', default='HI-Large')
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a fresh output directory')

    dataset = ds.dataset(args.curated, format='parquet', partitioning='hive', exclude_invalid_files=True)

    # Pass 1: every original source_row_index (cheap - a single int64 column).
    chunks = [batch.column(0).to_numpy(zero_copy_only=False)
              for batch in dataset.to_batches(columns=['source_row_index'], batch_size=1_000_000)]
    order = np.sort(np.concatenate(chunks))
    if len(np.unique(order)) != len(order):
        raise ValueError('Duplicate source_row_index values in subset')
    total = len(order)
    print(f'Pass 1: {total:,} rows, remapping to dense 0..{total - 1:,}', flush=True)

    args.output.mkdir(parents=True)
    written = 0
    day_dirs = sorted(p for p in args.curated.iterdir() if p.is_dir() and p.name.startswith('txn_date='))
    for day_dir in day_dirs:
        table = ds.dataset(day_dir, format='parquet').to_table()
        original_idx = table.column('source_row_index').to_numpy(zero_copy_only=False)
        new_idx = np.searchsorted(order, original_idx)
        if not np.array_equal(order[new_idx], original_idx):
            raise ValueError(f'Index remap failed for {day_dir.name}')
        table = table.set_column(table.schema.get_field_index('source_row_index'), 'source_row_index',
                                 pa.array(new_idx, type=pa.int64()))
        new_ids = [f'{args.subset}:{i}' for i in new_idx]
        table = table.set_column(table.schema.get_field_index('transaction_id'), 'transaction_id',
                                 pa.array(new_ids, type=pa.string()))
        out_dir = args.output / day_dir.name
        out_dir.mkdir(parents=True)
        pq.write_table(table, out_dir / 'part-00000.parquet')
        written += table.num_rows
        print(f'Wrote {day_dir.name}: {table.num_rows:,} rows (total {written:,})', flush=True)

    (args.output / '_SUCCESS').touch()
    if written != total:
        raise ValueError(f'Row count mismatch after remap: wrote {written:,}, expected {total:,}')
    print(f'DONE rows={written:,}', flush=True)


if __name__ == '__main__':
    main()
