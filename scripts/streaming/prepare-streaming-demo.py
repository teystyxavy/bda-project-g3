"""Prepare a complete sender-history CSV replay and compare every feature/score locally."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from bda_pipeline.inference import worker_ranker,score_frame
from bda_pipeline.model import digest
from bda_pipeline.orchestration import MANIFEST
from bda_pipeline.streaming import INPUT_COLUMNS,MemoryStore,account_key,canonical,process_event
from bda_pipeline.batch import FEATURES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-id',default='stream-smoke-20261006')
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('Use a fresh output directory')
    ranker=worker_ranker(str(args.artifacts),MANIFEST,threads=1)
    if digest(args.reference)!=ranker.selection['source_sha256']: raise ValueError('Frozen source mismatch')
    counts={}
    for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100000,columns=['from_bank','from_account']):
        for bank,account in batch.to_pandas().itertuples(index=False,name=None):
            key=account_key(bank,account);counts[key]=counts.get(key,0)+1
    # Label-independent deterministic selection; full history, never first-N rows from busy accounts.
    senders=set(sorted(key for key,count in counts.items() if 20<=count<=40)[:32])
    chunks=[]
    for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100000,columns=[*INPUT_COLUMNS,*FEATURES]):
        frame=batch.to_pandas()
        keys=[account_key(bank,account) for bank,account in zip(frame.from_bank,frame.from_account)]
        chunks.append(frame.loc[[key in senders for key in keys]])
    source=pd.concat(chunks,ignore_index=True).sort_values(['timestamp','source_row_index'])
    if not 1<=len(source)<=2000: raise ValueError('Invalid bounded replay cohort')
    baseline=score_frame(source,ranker).set_index('transaction_id')
    store=MemoryStore();ordinals={};events=[];results=[]
    def predict(txn):
        frame=pd.DataFrame([txn]);frame.timestamp=pd.to_datetime(frame.timestamp)
        return score_frame(frame,ranker).model_score.iloc[0]
    for row in source.to_dict('records'):
        txn={key:row[key] for key in INPUT_COLUMNS};txn['timestamp']=txn['timestamp'].isoformat()
        sender=account_key(txn['from_bank'],txn['from_account']);ordinals[sender]=ordinals.get(sender,0)+1
        event={'run_id':args.run_id,'ordinal':ordinals[sender],'transaction':txn}
        result=process_event(event,args.run_id,store,predict)
        expected=baseline.loc[txn['transaction_id']]
        if any(result['features'][name]!=expected[name] for name in FEATURES): raise ValueError('Streaming/batch behaviour feature mismatch')
        if not np.isclose(result['model_score'],expected.model_score,rtol=1e-6,atol=1e-7): raise ValueError('Streaming/batch score mismatch')
        events.append(event);results.append(result)
    # Replay duplicates after the complete cohort: no history/count changes.
    versions={key:value['version'] for key,value in store.states.items()}
    for event in events: process_event(event,args.run_id,store,predict)
    assert versions=={key:value['version'] for key,value in store.states.items()}
    args.output.mkdir(parents=True)
    pd.DataFrame([event['transaction']|{'run_id':event['run_id'],'ordinal':event['ordinal']} for event in events]).to_csv(args.output/'replay.csv',index=False)
    (args.output/'events.jsonl').write_text('\n'.join(canonical(e) for e in events)+'\n',encoding='utf-8')
    (args.output/'results.jsonl').write_text('\n'.join(canonical(r) for r in results)+'\n',encoding='utf-8')
    report={'status':'local-parity-passed-not-deployed','run_id':args.run_id,'rows':len(source),'senders':len(senders),
            'complete_sender_history':True,'all_features_exact':True,'scores_within_tolerance':True,'duplicate_replay_passed':True,
            'source_sha256':digest(args.reference),'manifest_sha256':MANIFEST,'replay_sha256':digest(args.output/'replay.csv'),
            'events_sha256':digest(args.output/'events.jsonl'),'results_sha256':digest(args.output/'results.jsonl'),
            'selection':'32 lexicographically first hashed senders with 20-40 total events; no labels used',
            'input':'Verified curated replay; existing prior-day FX values; not a raw transaction ingestion path'}
    (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
