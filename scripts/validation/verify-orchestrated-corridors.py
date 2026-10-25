"""Reconcile the approved Athena result locally, without another query.

Purpose
-------
Acceptance check for the BQ2 treasury branch of the Step Functions workflow.
The workflow ran the reviewed SQL ``sql/full-orchestrated-corridors.sql`` in
Athena (workgroup ``is459-g3-analysis``, database ``is459_g3_curated``) over
the curated Parquet. Its result CSV and the saved ``GetQueryExecution`` JSON
are downloaded, and this script recomputes the same corridor metrics
independently in pandas from the local reference Parquet, then compares:

* corridors of two types - ``bank`` (from_bank -> to_bank) and ``currency``
  (payment -> receiving currency) - per day;
* per corridor: transaction count (exact), USD volume (rtol 1e-10, atol
  USD 0.01), daily volume share and daily HHI (Herfindahl-Hirschman Index =
  sum of squared shares; 1 = all volume in one corridor, near 0 = spread out).

It first confirms the query SUCCEEDED and that its text equals the reviewed
SQL file (ignoring a trailing semicolon), so the check is about the right
query. Input files are hashed before and after the comparison to show they
did not change during verification.

Output: JSON report at ``--output`` (new path) with differences, hashes,
query execution ID and bytes scanned (Athena cost driver). No AWS calls.

Usage::

    python scripts/validation/verify-orchestrated-corridors.py --reference <curated.parquet> \\
        --result <athena.csv> --query-report <get-query-execution.json> --output report.json
"""
import argparse
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from bda_pipeline.common.integrity import digest
# Shared BQ2 reconciliation helpers (also used by the export stage).
from bda_pipeline.treasury.treasury_verification import KEYS, compare_corridors, expected_corridors, same_query


def main():
    """Validate query metadata, recompute corridors locally and compare."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['reference', 'result', 'query-report', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--expected-rows', type=int, default=5_078_345)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a fresh report path')
    # utf-8-sig: tolerate a BOM in JSON saved from PowerShell.
    query = json.loads(args.query_report.read_text(encoding='utf-8-sig'))['QueryExecution']
    # Path of the reviewed SQL file in the repository.
    sql_path = Path(__file__).resolve().parents[2] / 'sql/full-orchestrated-corridors.sql'
    if (query['Status']['State'] != 'SUCCEEDED' or not same_query(query['Query'], sql_path.read_text(encoding='utf-8'))
            or query['WorkGroup'] != 'is459-g3-analysis'
            or query['QueryExecutionContext']['Database'] != 'is459_g3_curated'):
        raise ValueError('Unexpected or unsuccessful treasury query')
    # Hash inputs up front; re-checked after the comparison.
    hashes = {'reference': digest(args.reference), 'result': digest(args.result),
              'query_report': digest(args.query_report), 'sql': digest(sql_path)}
    # Only the columns needed for corridor sums, streamed in 100,000-row batches.
    columns = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency', 'amount_usd']
    batches = (batch.to_pandas() for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100_000, columns=columns))
    expected = expected_corridors(batches, args.expected_rows)
    # Read corridor keys as strings so ids like '0012' keep leading zeros.
    actual = pd.read_csv(args.result, dtype={key: 'string' for key in KEYS})
    report = compare_corridors(expected, actual, args.expected_rows)
    if hashes['reference'] != digest(args.reference) or hashes['result'] != digest(args.result):
        raise ValueError('Verification input changed')
    report.update(query_execution_id=query['QueryExecutionId'], source_sha256=hashes,
                  bytes_scanned=query['Statistics']['DataScannedInBytes'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
