"""Prepare a complete sender-history CSV replay and compare every feature/score locally.

Purpose
-------
First step of the bounded streaming demo. Before anything is sent to AWS,
this script picks a small cohort of senders, builds the replay events and the
preloaded receiver states, and proves *locally* that the streaming code path
(``process_event``, which computes trailing features one event at a time from
per-sender and per-receiver state) gives exactly the same features and
(within tolerance) the same model scores as the batch pipeline. Only a
package that passes is later replayed into Kinesis by
``replay-streaming-demo.py``.

Why complete sender histories?
    Rolling features such as ``txn_count_7d`` depend on all earlier events of
    the same sender. If we replayed only part of a busy sender's history, the
    streaming features would be wrong. So whole senders are selected: the 32
    lexicographically first hashed sender keys that have between 20 and 40
    transactions in total. The choice depends only on hashed IDs and counts,
    never on laundering labels, so it does not bias the demo towards fraud.

Why preloaded receivers?
    Incoming features such as ``in_txn_count_7d`` depend on every payment into
    the receiver, including payments from senders outside the replay. Those
    are taken from the same verified batch data and written to DynamoDB once,
    before the replay (``receivers.jsonl``, seeded by
    ``seed-streaming-receivers.py``). Replayed payments then arrive through
    Kinesis and are added as they are scored.

Inputs
    ``--reference``: the curated, scored batch Parquet (full dataset). Its
    SHA-256 must equal the frozen model's training source, or the reviewed
    ``--reference-sha256`` when the reference is a later verified batch run
    of the same dataset.
    ``--artifacts``/``--manifest-sha256``: the frozen model release, verified
    against its manifest hash (default: the pinned ``MANIFEST``).

Outputs (``--output``, a fresh directory)
    ``replay.csv``      - one row per event (inputs + run_id + ordinal);
    ``events.jsonl``    - canonical JSON events, in replay order;
    ``receivers.jsonl`` - preloaded receiver states ({"pk", "state"} per line);
    ``results.jsonl``   - expected Lambda result per transaction;
    ``verification.json`` - status, counts and SHA-256 of every file.

No AWS calls are made.

Usage::

    python scripts/streaming/prepare-streaming-demo.py --reference <scored.parquet> \\
        --artifacts <model dir> --manifest-sha256 <sha> --reference-sha256 <sha> \\
        --run-id stream-v2-20261107 --output output/streaming-demo-v2-20261107
"""
import argparse
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

# worker_ranker loads + verifies the frozen XGBoost model; score_frame scores a DataFrame.
from bda_pipeline.inference import model_features,score_frame,worker_ranker
from bda_pipeline.model import digest
from bda_pipeline.orchestration import MANIFEST
# Streaming helpers: INPUT_COLUMNS (raw scoring inputs without the rolling
# features or labels), MemoryStore (in-memory stand-in for DynamoDB),
# account_key (hashed account id), canonical (sorted compact JSON),
# process_event (validate -> update sender/receiver state -> score -> commit),
# receiver_seeds (preloaded receiver states from batch rows).
from bda_pipeline.streaming import (INPUT_COLUMNS,STREAM_FEATURES,MemoryStore,account_key,canonical,
                                    process_event,receiver_seeds)
# All behaviour feature names computed by the batch pipeline.
from bda_pipeline.batch import FEATURES

PARTIES=['transaction_id','timestamp','from_bank','from_account','to_bank','to_account']


def main():
    """Select the cohort, run local streaming/batch parity and write the package.

    Raises:
        ValueError: on a reference hash mismatch, an out-of-bounds cohort, a
            model needing features streaming does not compute, or any
            feature/score mismatch between streaming and batch.
        AssertionError: if replaying duplicates changes any state.
    """
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--reference-sha256')
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--manifest-sha256',default=MANIFEST)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--run-id',default='stream-smoke-20261006')
    args=parser.parse_args()
    # Never overwrite a previously verified package.
    if args.output.exists(): raise ValueError('Use a fresh output directory')
    # One model thread keeps scoring deterministic and matches the Lambda setting.
    ranker=worker_ranker(str(args.artifacts),args.manifest_sha256,threads=1)
    if set(model_features(ranker))-set(STREAM_FEATURES):
        raise ValueError('The frozen model reads behaviour features that streaming does not compute')
    # The reference is pinned: the model's training source, or an explicitly reviewed later batch output.
    source_sha256=digest(args.reference)
    if source_sha256!=(args.reference_sha256 or ranker.selection['source_sha256']): raise ValueError('Reference source mismatch')
    key=lru_cache(maxsize=None)(account_key)
    # Pass 1: count transactions per hashed sender, streaming the Parquet in
    # 100,000-row batches (only two columns) to keep memory small.
    counts={}
    for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100000,columns=['from_bank','from_account']):
        for bank,account in batch.to_pandas().itertuples(index=False,name=None):
            sender=key(bank,account);counts[sender]=counts.get(sender,0)+1
    # Label-independent deterministic selection; full history, never first-N rows from busy accounts.
    senders=set(sorted(sender for sender,count in counts.items() if 20<=count<=40)[:32])
    chunks=[]
    # Pass 2: keep only rows from the selected senders, with input + feature columns.
    for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100000,columns=[*INPUT_COLUMNS,*FEATURES]):
        frame=batch.to_pandas()
        chunks.append(frame.loc[[key(bank,account) in senders for bank,account in zip(frame.from_bank,frame.from_account)]])
    # Replay order = event time, ties broken by original source row order.
    source=pd.concat(chunks,ignore_index=True).sort_values(['timestamp','source_row_index'])
    # Safety bound: the demo is meant to be small (cost and review effort).
    if not 1<=len(source)<=2000: raise ValueError('Invalid bounded replay cohort')
    # Pass 3: every payment into a receiver the cohort pays, from the full dataset.
    receivers={key(bank,account) for bank,account in zip(source.to_bank,source.to_account)}
    chunks=[]
    for batch in pq.ParquetFile(args.reference).iter_batches(batch_size=100000,columns=PARTIES):
        frame=batch.to_pandas()
        chunks.append(frame.loc[[key(bank,account) in receivers for bank,account in zip(frame.to_bank,frame.to_account)]])
    inbound=pd.concat(chunks,ignore_index=True)
    replayed=set(source.transaction_id)
    seeds={args.run_id+':receiver:'+receiver:state for receiver,state in sorted(receiver_seeds(inbound,replayed).items())}
    if len(seeds)!=len(receivers): raise ValueError('Receiver preload does not cover the cohort')
    # Batch scores for the cohort (all batch features, as the Glue job scores) = the "truth" streaming must match.
    baseline=score_frame(source,ranker).set_index('transaction_id')
    # Per-sender ordinal counters number each sender's events 1, 2, 3, ...
    # The streaming state requires them in order (gaps or reordering fail closed).
    store=MemoryStore(seeds);ordinals={};events=[];results=[]
    def predict(txn):
        """Score one transaction dict (inputs + streaming features) with the frozen model."""
        frame=pd.DataFrame([txn]);frame.timestamp=pd.to_datetime(frame.timestamp)
        return score_frame(frame,ranker,STREAM_FEATURES).model_score.iloc[0]
    # Feed every event through the streaming path in replay order.
    for row in source.to_dict('records'):
        # Timestamps become ISO strings, exactly as they will travel in JSON.
        txn={name:row[name] for name in INPUT_COLUMNS};txn['timestamp']=txn['timestamp'].isoformat()
        sender=key(txn['from_bank'],txn['from_account']);ordinals[sender]=ordinals.get(sender,0)+1
        event={'run_id':args.run_id,'ordinal':ordinals[sender],'transaction':txn}
        result=process_event(event,args.run_id,store,predict)
        # Features are integer counts, so they must match exactly; scores are
        # floats and may differ by tiny rounding (rtol 1e-6, atol 1e-7).
        expected=baseline.loc[txn['transaction_id']]
        if any(result['features'][name]!=expected[name] for name in STREAM_FEATURES): raise ValueError('Streaming/batch behaviour feature mismatch')
        if not np.isclose(result['model_score'],expected.model_score,rtol=1e-6,atol=1e-7): raise ValueError('Streaming/batch score mismatch')
        events.append(event);results.append(result)
    # Every receiver must have applied exactly the replayed payments it was sent.
    arrivals={}
    for event in events:
        receiver=args.run_id+':receiver:'+key(event['transaction']['to_bank'],event['transaction']['to_account'])
        arrivals[receiver]=arrivals.get(receiver,0)+1
    if any(store.states[receiver]['version']!=count for receiver,count in arrivals.items()): raise ValueError('Receiver version mismatch')
    # Idempotency check: re-sending every event must return the stored results
    # without advancing any sender or receiver version (Kinesis can deliver duplicates).
    versions={name:value['version'] for name,value in store.states.items()}
    for event in events: process_event(event,args.run_id,store,predict)
    assert versions=={name:value['version'] for name,value in store.states.items()}
    difference=max(abs(result['model_score']-baseline.loc[result['transaction_id']].model_score) for result in results)
    args.output.mkdir(parents=True)
    # Write the package: CSV for the producer, JSONL events, receiver preload and expected results.
    pd.DataFrame([event['transaction']|{'run_id':event['run_id'],'ordinal':event['ordinal']} for event in events]).to_csv(args.output/'replay.csv',index=False)
    (args.output/'events.jsonl').write_text('\n'.join(canonical(e) for e in events)+'\n',encoding='utf-8')
    (args.output/'receivers.jsonl').write_text('\n'.join(canonical({'pk':pk,'state':state}) for pk,state in seeds.items())+'\n',encoding='utf-8')
    (args.output/'results.jsonl').write_text('\n'.join(canonical(r) for r in results)+'\n',encoding='utf-8')
    # The status string is checked by replay-streaming-demo.py before any send;
    # the SHA-256 values let later steps detect any modification of the files.
    report={'status':'local-parity-passed-not-deployed','run_id':args.run_id,'rows':len(source),'senders':len(senders),
            'receivers':len(seeds),'preloaded_receiver_payments':sum(len(state['history']) for state in seeds.values()),
            'features':STREAM_FEATURES,'model_features':model_features(ranker),
            'complete_sender_history':True,'complete_receiver_history':True,'all_features_exact':True,
            'scores_within_tolerance':True,'max_absolute_score_difference':float(difference),'duplicate_replay_passed':True,
            'source_sha256':source_sha256,'training_source_sha256':ranker.selection['source_sha256'],
            'manifest_sha256':args.manifest_sha256,'replay_sha256':digest(args.output/'replay.csv'),
            'events_sha256':digest(args.output/'events.jsonl'),'receivers_sha256':digest(args.output/'receivers.jsonl'),
            'results_sha256':digest(args.output/'results.jsonl'),
            'selection':'32 lexicographically first hashed senders with 20-40 total events; no labels used',
            'receiver_preload':'Every other payment into each receiver the cohort pays, from the same verified batch data',
            'input':'Verified curated replay; existing prior-day FX values; not a raw transaction ingestion path'}
    (args.output/'verification.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
