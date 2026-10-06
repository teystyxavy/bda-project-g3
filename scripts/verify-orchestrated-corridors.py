"""Reconcile the approved Athena result locally, without another query."""
import argparse
import json
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from bda_pipeline.model import digest
from bda_pipeline.treasury_verification import KEYS, compare_corridors, expected_corridors, same_query


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['reference', 'result', 'query-report', 'output']:
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--expected-rows', type=int, default=5_078_345)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Use a fresh report path')
    query = json.loads(args.query_report.read_text(encoding='utf-8-sig'))['QueryExecution']
    sql_path = Path(__file__).resolve().parents[1] / 'sql/full-orchestrated-corridors.sql'
    if (query['Status']['State'] != 'SUCCEEDED' or not same_query(query['Query'], sql_path.read_text(encoding='utf-8'))
            or query['WorkGroup'] != 'is459-g3-analysis'
            or query['QueryExecutionContext']['Database'] != 'is459_g3_curated'):
        raise ValueError('Unexpected or unsuccessful treasury query')
    hashes = {'reference': digest(args.reference), 'result': digest(args.result),
              'query_report': digest(args.query_report), 'sql': digest(sql_path)}
    columns = ['txn_date', 'from_bank', 'to_bank', 'payment_currency', 'receiving_currency', 'amount_usd']
    batches = (batch.to_pandas() for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100_000, columns=columns))
    expected = expected_corridors(batches, args.expected_rows)
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
