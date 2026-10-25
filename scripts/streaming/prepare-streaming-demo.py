"""Prepare a complete sender-history CSV replay and compare every feature/score locally.

Purpose
-------
First step of the bounded streaming demo. Before anything is sent to AWS,
this script picks a small cohort of senders, builds the replay events, and
proves *locally* that the streaming code path (``process_event``, which
computes trailing features one event at a time from per-sender state)
gives exactly the same features and (within tolerance) the same model
scores as the batch pipeline. Only a package that passes is later replayed
into Kinesis by ``replay-streaming-demo.py``.

Why complete sender histories?
    Rolling features such as ``txn_count_7d`` depend on all earlier events of
    the same sender. If we replayed only part of a busy sender's history, the
    streaming features would be wrong. So whole senders are selected: the 32
    lexicographically first hashed sender keys that have between 20 and 40
    transactions in total. The choice depends only on hashed IDs and counts,
    never on laundering labels, so it does not bias the demo towards fraud.

Inputs
    ``--reference``: the curated, scored batch Parquet whose SHA-256 must equal
    the source hash recorded in the frozen model's selection file.
    ``--artifacts``: the frozen model release (verified against ``MANIFEST``).

Outputs (``--output``, a fresh directory)
    ``replay.csv``      - one row per event (inputs + run_id + ordinal);
    ``events.jsonl``    - canonical JSON events, in replay order;
    ``results.jsonl``   - expected Lambda result per transaction;
    ``verification.json`` - status, counts and SHA-256 of every file.

No AWS calls are made.

Usage::

    python scripts/streaming/prepare-streaming-demo.py --reference <scored.parquet> \\
        --artifacts <model dir> --output output/streaming-demo-20261006
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# worker_ranker loads + verifies the frozen XGBoost model; score_frame scores a DataFrame.
from bda_pipeline.scoring.inference import worker_ranker,score_frame
from bda_pipeline.common.integrity import digest
from bda_pipeline.control.orchestration import MANIFEST
# Streaming helpers: INPUT_COLUMNS (raw scoring inputs without the rolling
# features or labels), MemoryStore (in-memory stand-in for DynamoDB),
# account_key (hashed sender id), canonical (sorted compact JSON),
# process_event (validate -> update sender state -> score -> commit).
from bda_pipeline.scoring.streaming import INPUT_COLUMNS,MemoryStore,account_key,canonical,process_event
# The five behaviour feature names, shared with the batch pipeline.
from bda_pipeline.features.behaviour import FEATURES


def main():
    """Select the cohort, run local streaming/batch parity and write the package.

    Raises:
        ValueError: on a frozen-source hash mismatch, an out-of-bounds cohort,
            or any feature/score mismatch between streaming and batch.
        AssertionError: if replaying duplicates changes any sender state.
    """
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-id',default='stream-smoke-20261006')
    args=parser.parse_args()
    # Never overwrite a previously verified package.
    if args.output.exists(): raise ValueError('Use a fresh output directory')
    # One model thread keeps scoring deterministic and matches the Lambda setting.
    ranker=worker_ranker(str(args.artifacts),MANIFEST,threads=1)
    # The model was trained/evaluated on one exact reference file; refuse any other.
    if digest(args.reference)!=ranker.selection['source_sha256']: raise ValueError('Frozen source mismatch')
    # Pass 1: count transactions per hashed sender, streaming the Parquet in
    # 100,000-row batches (only two columns) to keep memory small.
    counts={}
    for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100000,columns=['from_bank','from_account']):
        for bank,account in batch.to_pandas().itertuples(index=False,name=None):
            key=account_key(bank,account);counts[key]=counts.get(key,0)+1
    # Label-independent deterministic selection; full history, never first-N rows from busy accounts.
    senders=set(sorted(key for key,count in counts.items() if 20<=count<=40)[:32])
    chunks=[]
    # Pass 2: keep only rows from the selected senders, with input + feature columns.
    for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100000,columns=[*INPUT_COLUMNS,*FEATURES]):
        frame=batch.to_pandas()
        keys=[account_key(bank,account) for bank,account in zip(frame.from_bank,frame.from_account)]
        chunks.append(frame.loc[[key in senders for key in keys]])
    # Replay order = event time, ties broken by original source row order.
    source=pd.concat(chunks,ignore_index=True).sort_values(['timestamp','source_row_index'])
    # Safety bound: the demo is meant to be small (cost and review effort).
    if not 1<=len(source)<=2000: raise ValueError('Invalid bounded replay cohort')
    # Batch scores for the cohort = the "truth" the streaming path must match.
    baseline=score_frame(source,ranker).set_index('transaction_id')
    # Per-sender ordinal counters number each sender's events 1, 2, 3, ...
    # The streaming state requires them in order (gaps or reordering fail closed).
    store=MemoryStore();ordinals={};events=[];results=[]
    def predict(txn):
        """Score one transaction dict (inputs + streaming features) with the frozen model."""
        frame=pd.DataFrame([txn]);frame.timestamp=pd.to_datetime(frame.timestamp)
        return score_frame(frame,ranker).model_score.iloc[0]
    # Feed every event through the streaming path in replay order.
    for row in source.to_dict('records'):
        # Timestamps become ISO strings, exactly as they will travel in JSON.
        txn={key:row[key] for key in INPUT_COLUMNS};txn['timestamp']=txn['timestamp'].isoformat()
        sender=account_key(txn['from_bank'],txn['from_account']);ordinals[sender]=ordinals.get(sender,0)+1
        event={'run_id':args.run_id,'ordinal':ordinals[sender],'transaction':txn}
        result=process_event(event,args.run_id,store,predict)
        # Features are integer counts, so they must match exactly; scores are
        # floats and may differ by tiny rounding (rtol 1e-6, atol 1e-7).
        expected=baseline.loc[txn['transaction_id']]
        if any(result['features'][name]!=expected[name] for name in FEATURES): raise ValueError('Streaming/batch behaviour feature mismatch')
        if not np.isclose(result['model_score'],expected.model_score,rtol=1e-6,atol=1e-7): raise ValueError('Streaming/batch score mismatch')
        events.append(event);results.append(result)
    # Idempotency check: re-sending every event must return the stored results
    # without advancing any sender's version (Kinesis can deliver duplicates).
    # Replay duplicates after the complete cohort: no history/count changes.
    versions={key:value['version'] for key,value in store.states.items()}
    for event in events: process_event(event,args.run_id,store,predict)
    assert versions=={key:value['version'] for key,value in store.states.items()}
    args.output.mkdir(parents=True)
    # Write the package: CSV for the producer, JSONL events and expected results.
    pd.DataFrame([event['transaction']|{'run_id':event['run_id'],'ordinal':event['ordinal']} for event in events]).to_csv(args.output/'replay.csv',index=False)
    (args.output/'events.jsonl').write_text('\n'.join(canonical(e) for e in events)+'\n',encoding='utf-8')
    (args.output/'results.jsonl').write_text('\n'.join(canonical(r) for r in results)+'\n',encoding='utf-8')
    # The status string is checked by replay-streaming-demo.py before any send;
    # the SHA-256 values let later steps detect any modification of the files.
    report={'status':'local-parity-passed-not-deployed','run_id':args.run_id,'rows':len(source),'senders':len(senders),
            'complete_sender_history':True,'all_features_exact':True,'scores_within_tolerance':True,'duplicate_replay_passed':True,
            'source_sha256':digest(args.reference),'manifest_sha256':MANIFEST,'replay_sha256':digest(args.output/'replay.csv'),
            'events_sha256':digest(args.output/'events.jsonl'),'results_sha256':digest(args.output/'results.jsonl'),
            'selection':'32 lexicographically first hashed senders with 20-40 total events; no labels used',
            'input':'Verified curated replay; existing prior-day FX values; not a raw transaction ingestion path'}
    (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
