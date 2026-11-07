import base64,copy,json
from datetime import datetime,timedelta

import pytest
from bda_pipeline.streaming import (MemoryStore,RECEIVER_FEATURES,account_key,canonical,handle_records,process_event,
                                    receive,receiver_seeds,transition)

RUN='stream-smoke-20261006'

def event(ordinal=1,minute=0,index=None,bank='001',account='0009',target='0002'):
    index=index if index is not None else ordinal
    return {'run_id':RUN,'ordinal':ordinal,'transaction':{'transaction_id':'HI-Small:'+str(index),'source_row_index':index,
            'timestamp':(datetime(2022,9,1)+timedelta(minutes=minute)).isoformat(),'from_bank':bank,'from_account':account,
            'to_bank':'2','to_account':target,'amount_usd':10.,'amount_usd_asof':9.,'payment_format':'ACH',
            'payment_currency':'Euro','receiving_currency':'Euro'}}


def seeded(*targets,history=()):
    """A store whose receivers (bank 2, given accounts) are preloaded, as the replay setup does."""
    return {RUN+':receiver:'+account_key('2',target):{'version':0,'last_time':-1,'history':sorted(map(list,history))}
            for target in targets or ['0002']}


def test_composite_identity_normalizes_bank_only():
    assert account_key('001','0009')==account_key('1','0009')
    assert account_key('1','0009')!=account_key('2','0009')
    assert account_key('1','0009')!=account_key('1','9')


def test_peer_exclusion_across_batches_and_window_boundaries():
    store=MemoryStore(seeded())
    first=process_event(event(),RUN,store,lambda _:0.2)
    peer=process_event(event(2,0),RUN,store,lambda _:0.2)
    assert first['features']==peer['features'] and first['features']['txn_count_1h']==0
    boundary=process_event(event(3,60),RUN,store,lambda _:0.2)
    assert boundary['features']['txn_count_1h']==2
    after=process_event(event(4,61),RUN,store,lambda _:0.2)
    assert after['features']['txn_count_1h']==1 and after['features']['txn_count_24h']==3


def test_duplicate_after_commit_repairs_failed_audit_without_rescoring():
    class BrokenAudit(MemoryStore):
        def audit(self,key,result):
            if not self.audits:
                self.audits['attempt']=True
                raise RuntimeError('S3 unavailable')
            super().audit(key,result)
    store=BrokenAudit(seeded())
    with pytest.raises(RuntimeError): process_event(event(),RUN,store,lambda _:0.3)
    result=process_event(event(),RUN,store,lambda _:pytest.fail('Duplicate must not predict'))
    assert result['model_score']==0.3 and all(state['version']==1 for state in store.states.values())
    changed=event();changed['transaction']['amount_usd_asof']=11
    with pytest.raises(ValueError,match='changed payload'):process_event(changed,RUN,store,lambda _:0.3)


@pytest.mark.parametrize('bad',[event(2),event(1,-1)])
def test_missing_or_out_of_order_event_rejected(bad):
    with pytest.raises(ValueError):process_event(bad,RUN,MemoryStore(seeded()),lambda _:0.3)


def test_older_timestamp_and_state_overflow_fail_closed():
    store=MemoryStore(seeded());process_event(event(1,2),RUN,store,lambda _:0.3)
    with pytest.raises(ValueError,match='out-of-order'):process_event(event(2,1),RUN,store,lambda _:0.3)
    state={'version':1000,'last_time':1661990400,'history':[[1661990400,'a'*64]]*1000}
    with pytest.raises(ValueError,match='capacity'):transition(state,event(1001),RUN)


def test_labels_and_precomputed_features_rejected():
    for name in ['is_laundering','txn_count_1h']:
        changed=event();changed['transaction'][name]=1
        with pytest.raises(ValueError,match='Unexpected'):process_event(changed,RUN,MemoryStore(),lambda _:0.3)


def test_first_failed_sequence_stops_suffix():
    events=[event(1),event(3,index=3),event(2,index=2)]
    records=[{'kinesis':{'sequenceNumber':str(i+100),'partitionKey':account_key('001','0009'),
                         'data':base64.b64encode(canonical(e).encode()).decode()}} for i,e in enumerate(events)]
    store=MemoryStore(seeded())
    response=handle_records({'Records':records},RUN,store,lambda _:0.3)
    assert response=={'batchItemFailures':[{'itemIdentifier':'101'}]}
    assert len(store.results)==1


def test_receiver_features_count_preloaded_and_replayed_payments():
    start=int((datetime(2022,9,1)-datetime(1970,1,1)).total_seconds())
    # Preloaded: one payment 2 days earlier (sender x) and one at the same second as the first replayed payment (peer).
    store=MemoryStore(seeded(history=[(start-2*86400,'x'),(start,'y')]))
    first=process_event(event(1,0),RUN,store,lambda _:0.2)
    assert {name:first['features'][name] for name in RECEIVER_FEATURES}=={
        'in_txn_count_1h':0,'in_txn_count_24h':0,'in_txn_count_7d':1,'distinct_senders_24h':0,'distinct_senders_7d':1}
    # A second sender pays the same receiver 30 minutes later: sees the peer, the first replayed payment and x.
    second=process_event(event(1,30,index=2,account='0010'),RUN,store,lambda _:0.2)
    assert second['features']['in_txn_count_1h']==2 and second['features']['in_txn_count_7d']==3
    assert second['features']['distinct_senders_24h']==2 and second['features']['txn_count_1h']==0
    receiver=store.states[RUN+':receiver:'+account_key('2','0002')]
    assert receiver['version']==2 and len(receiver['history'])==4


def test_receiver_without_preload_fails_closed_and_commits_nothing():
    store=MemoryStore()
    with pytest.raises(ValueError,match='not preloaded'):process_event(event(),RUN,store,lambda _:0.2)
    assert store.states=={} and store.results=={}


def test_out_of_order_receiver_event_from_another_sender_rejected():
    store=MemoryStore(seeded());process_event(event(1,10),RUN,store,lambda _:0.2)
    with pytest.raises(ValueError,match='Out-of-order receiver'):
        process_event(event(1,5,index=2,account='0010'),RUN,store,lambda _:0.2)
    assert len(store.results)==1


def test_concurrent_receiver_update_aborts_the_whole_commit():
    class Racing(MemoryStore):
        def state(self,key):
            value=super().state(key)
            if ':receiver:' in key: self.states[key]['version']+=1
            return value
    store=Racing(seeded())
    with pytest.raises(ValueError,match='Concurrent'):process_event(event(),RUN,store,lambda _:0.2)
    assert store.results=={} and not any(':sender:' in key for key in store.states)


def test_receiver_seeds_hold_only_payments_outside_the_replay():
    import pandas as pd
    rows=pd.DataFrame([
        {'transaction_id':'HI-Small:1','timestamp':pd.Timestamp('2022-09-01 00:00'),'from_bank':'001','from_account':'A','to_bank':'2','to_account':'R'},
        {'transaction_id':'HI-Small:2','timestamp':pd.Timestamp('2022-09-01 01:00'),'from_bank':'5','from_account':'B','to_bank':'02','to_account':'R'},
        {'transaction_id':'HI-Small:3','timestamp':pd.Timestamp('2022-09-02 00:00'),'from_bank':'5','from_account':'B','to_bank':'3','to_account':'S'}])
    seeds=receiver_seeds(rows,{'HI-Small:1'})
    assert list(seeds)==[account_key('2','R')]
    assert seeds[account_key('2','R')]=={'version':0,'last_time':-1,'history':[[1661994000,account_key('5','B')]]}
