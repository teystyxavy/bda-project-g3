"""Verify downloaded stream audit against the prepared complete-history cohort."""
import argparse,json
from pathlib import Path
import numpy as np
from bda_pipeline.model import digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['package','audit','receipt','output']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Use a fresh verification report')
    package=json.loads((args.package/'verification.json').read_text(encoding='utf-8'))
    expected_path=args.package/'results.jsonl'
    if digest(expected_path)!=package['results_sha256']:raise ValueError('Prepared result changed')
    expected={r['transaction_id']:r for r in map(json.loads,expected_path.read_text(encoding='utf-8').splitlines())}
    receipts=list(map(json.loads,args.receipt.read_text(encoding='utf-8').splitlines()))
    if len(receipts)!=len(expected) or {r['transaction_id'] for r in receipts}!=set(expected):raise ValueError('Incomplete producer receipt')
    actual={};paths=list(args.audit.rglob('*.json'))
    if len(paths)!=len(expected):raise ValueError('Cloud audit count mismatch')
    maximum=0.
    for path in paths:
        row=json.loads(path.read_text(encoding='utf-8'))
        if row['transaction_id'] in actual or row['transaction_id'] not in expected:raise ValueError('Duplicate/unknown cloud identity')
        native=expected[row['transaction_id']]
        if set(row)!=set(native) or any(row[k]!=native[k] for k in native if k!='model_score'):raise ValueError('Cloud input/feature/run mismatch')
        if not np.isclose(row['model_score'],native['model_score'],rtol=1e-6,atol=1e-7):raise ValueError('Cloud score mismatch')
        maximum=max(maximum,abs(row['model_score']-native['model_score']));actual[row['transaction_id']]=row
    report={'status':'passed','rows':len(actual),'run_id':package['run_id'],'features_exact':True,'max_absolute_score_difference':maximum,
            'receipt_sha256':digest(args.receipt),'audit_sha256':{str(path.relative_to(args.audit)):digest(path) for path in paths}}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'passed','rows':len(actual),'max_absolute_score_difference':maximum}))


if __name__=='__main__':main()
