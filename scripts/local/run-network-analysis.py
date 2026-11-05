"""Run the BQ2 mule-network analysis locally and (optionally) preview it on the dashboard.

Where this sits in the pipeline
-------------------------------
In AWS the Glue job ``jobs/network_mules.py`` writes the network tables and the
SageMaker export stage checks them against ``bda_pipeline.network``. This
script runs the same reference analysis on a laptop:

1. ``analyse_network`` over a curated Parquet folder (for example the output of
   ``jobs/curate_transactions.py`` run with local Spark);
2. with ``--glue-output``, ``compare_network`` against tables written by the
   Spark job (run locally or downloaded from ``pipeline/runs/<run>/network/``);
3. writes ``network.json`` (the dashboard section) and ``summary.json``;
4. with ``--snapshot``, merges the section into an existing ``dashboard.json``
   (treasury views removed) and copies the dashboard assets, so
   ``python -m http.server -d <output>`` shows the Mule networks tab.

Usage
-----
    python scripts/local/run-network-analysis.py --curated data/curated \\
        --glue-output data/network --snapshot dashboard.json --output output/network-preview
"""
import argparse
import json
import shutil
import time
from pathlib import Path

from bda_pipeline.network import analyse_network, compare_network, dashboard_section, parquet_batches, read_tables

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--curated', type=Path, required=True, help='Curated Parquet folder (hive-partitioned by txn_date)')
    parser.add_argument('--glue-output', type=Path, help='Network tables written by jobs/network_mules.py, to verify')
    parser.add_argument('--snapshot', type=Path, help='Existing dashboard.json to merge the network section into')
    parser.add_argument('--output', type=Path, required=True, help='Fresh output folder')
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Use a fresh output folder')
    started = time.perf_counter()
    result = analyse_network(parquet_batches(args.curated))
    rows = int(result['totals'].rows.iloc[0])
    section = dashboard_section(result)
    if args.glue_output:
        section['verification'] = compare_network(result, read_tables(args.glue_output), rows)
    args.output.mkdir(parents=True)
    (args.output / 'network.json').write_text(json.dumps(section, allow_nan=False))
    summary = {key: section[key] for key in ['totals', 'thresholds', 'loops', 'rings', 'top_rings']}
    summary['seconds'] = round(time.perf_counter() - started, 1)
    summary['verification'] = section.get('verification', {'status': 'not-run'})
    (args.output / 'summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False))
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text())
        if snapshot.get('full_rows') not in (None, rows):
            raise SystemExit(f"Snapshot covers {snapshot['full_rows']} rows but the network covers {rows}")
        for key in ['treasury', 'treasury_daily']:
            snapshot.pop(key, None)
        snapshot.get('exploration', {}).pop('treasury', None)
        snapshot['network'] = section
        (args.output / 'dashboard.json').write_text(json.dumps(snapshot, allow_nan=False))
        for name in ['index.html', 'dashboard.js', 'dashboard.css']:
            shutil.copyfile(ROOT / 'dashboard' / name, args.output / name)
    print(json.dumps({'rows': rows, 'rings': section['rings'], 'verification': summary['verification'].get('status'),
                      'seconds': summary['seconds']}, indent=2))


if __name__ == '__main__':
    main()
