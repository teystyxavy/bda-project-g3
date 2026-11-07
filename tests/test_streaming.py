import base64,copy,json
from datetime import datetime,timedelta

import numpy as np
import pandas as pd
import pytest
from bda_pipeline.analytics import add_behaviour
from bda_pipeline.batch import FEATURES
from bda_pipeline.streaming import (MAX_HISTORY,MemoryStore,RECEIVER_FEATURES,STREAM_FEATURES,account_key,canonical,
                                    empty_scan,handle_records,process_event,receiver_seeds,transition)

RUN='stream-smoke-20261006'
START=int((datetime(2022,9,1)-datetime(1970,1,1)).total_seconds())

def event(ordinal=1,minute=0,index=None,bank='001',account='0009',target='0002',amount=9.):
    index=index if index is not None else ordinal
    return {'run_id':RUN,'ordinal':ordinal,'transaction':{'transaction_id':'HI-Small:'+str(index),'source_row_index':index,
            'timestamp':(datetime(2022,9,1)+timedelta(minutes=minute)).isoformat(),'from_bank':bank,'from_account':account,
            'to_bank':'2','to_account':target,'amount_usd':10.,'amount_usd_asof':amount,'payment_format':'ACH',
            'payment_currency':'Euro','receiving_currency':'Euro'}}


def seeded(*targets,pending=()):
    """A store whose receivers (bank 2, given accounts) are preloaded, as the replay setup does.
    ``pending`` rows are (epoch, source row, sender key, USD) from senders outside the replay."""
    return {RUN+':receiver:'+account_key('2',target):{'version':0,**empty_scan(),'pending':sorted(map(list,pending))}
            for target in targets or ['0002']}


def test_every_batch_feature_is_computed():
    assert STREAM_FEATURES==list(FEATURES)
    result=process_event(event(),RUN,MemoryStore(seeded()),lambda _:0.2)
    assert list(result['features'])==STREAM_FEATURES or set(result['features'])==set(FEATURES)


def test_composite_identity_normalizes_bank_only():
    assert account_key('001','0009')==account_key('1','0009')
    assert account_key('1','0009')!=account_key('2','0009')
    assert account_key('1','0009')!=account_key('1','9')


def test_other_curated_subsets_are_accepted_with_matching_identity():
    store=MemoryStore(seeded())
    medium=event(minute=60*24*20,index=27036075);medium['transaction']['transaction_id']='HI-Medium:27036075'
    assert process_event(medium,RUN,store,lambda _:0.2)['transaction_id']=='HI-Medium:27036075'
    for wrong in ['HI-Medium:1','Other:27036075','HI-Medium:027036075']:
        changed=copy.deepcopy(medium);changed['transaction']['transaction_id']=wrong
        with pytest.raises(ValueError,match='source identity'):process_event(changed,RUN,MemoryStore(seeded()),lambda _:0.2)


def test_peer_exclusion_across_batches_and_window_boundaries():
    store=MemoryStore(seeded())
    first=process_event(event(),RUN,store,lambda _:0.2)
    peer=process_event(event(2,0),RUN,store,lambda _:0.2)
    # Window features exclude same-timestamp peers; the prior-pair count includes earlier rows at the same time.
    assert {k:v for k,v in first['features'].items() if k!='corridor_prior_count'}=={k:v for k,v in peer['features'].items() if k!='corridor_prior_count'}
    assert first['features']['txn_count_1h']==0 and first['features']['corridor_prior_count']==0 and peer['features']['corridor_prior_count']==1
    boundary=process_event(event(3,60),RUN,store,lambda _:0.2)
    assert boundary['features']['txn_count_1h']==2 and boundary['features']['amount_sum_24h']==18.
    after=process_event(event(4,61),RUN,store,lambda _:0.2)
    assert after['features']['txn_count_1h']==1 and after['features']['txn_count_24h']==3 and after['features']['corridor_prior_count']==3


def test_streaming_matches_batch_scan_exactly_for_random_histories():
    """Complete sender histories plus preloaded receivers reproduce analytics.add_behaviour bit for bit."""
    rng=np.random.default_rng(459)
    n=600
    frame=pd.DataFrame({'source_row_index':np.arange(n),'timestamp':pd.Timestamp('2022-09-01')+pd.to_timedelta(np.sort(rng.integers(0,60*24*10,n)),unit='min'),
                        'from_bank':rng.choice(['001','1','2','03'],n),'from_account':rng.choice(list('ABCDEFG'),n),
                        'to_bank':rng.choice(['2','02','5'],n),'to_account':rng.choice(list('PQRS'),n),
                        'amount_usd_asof':np.round(rng.uniform(0,5000,n),2)})
    frame['transaction_id']='HI-Small:'+frame.source_row_index.astype(str)
    batch=add_behaviour(frame).set_index('transaction_id')
    senders={account_key('1','A'),account_key('2','B')}
    cohort=frame[[account_key(b,a) in senders for b,a in zip(frame.from_bank,frame.from_account)]].sort_values(['timestamp','source_row_index'])
    seeds={RUN+':receiver:'+key:state for key,state in receiver_seeds(frame,set(cohort.transaction_id)).items()}
    store=MemoryStore(seeds);ordinals={}
    for row in cohort.itertuples(index=False):
        sender=account_key(row.from_bank,row.from_account);ordinals[sender]=ordinals.get(sender,0)+1
        txn={**event()['transaction'],'transaction_id':row.transaction_id,'source_row_index':int(row.source_row_index),
             'timestamp':row.timestamp.isoformat(),'from_bank':row.from_bank,'from_account':row.from_account,
             'to_bank':row.to_bank,'to_account':row.to_account,'amount_usd_asof':float(row.amount_usd_asof)}
        result=process_event({'run_id':RUN,'ordinal':ordinals[sender],'transaction':txn},RUN,store,lambda _:0.2)
        assert result['features']=={name:batch.loc[row.transaction_id,name].item() for name in FEATURES}
    assert len(cohort)>50 and any(state['version'] for key,state in store.states.items() if ':receiver:' in key)


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
    with pytest.raises(ValueError,match='Out-of-order'):process_event(event(2,1),RUN,store,lambda _:0.3)
    state={'version':MAX_HISTORY,**empty_scan(),'pairs':{}}
    state['queues'][2]=[[START,'a'*64,1.]]*MAX_HISTORY;state['last']=[START,0]
    with pytest.raises(ValueError,match='capacity'):transition(state,event(MAX_HISTORY+1,index=MAX_HISTORY+1),RUN)


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
    # Preloaded: one payment 2 days earlier (sender x), one at the same second as the first replayed payment (peer, earlier
    # source row) and one later, which must stay pending until a replayed payment passes it.
    store=MemoryStore(seeded(pending=[(START-2*86400,0,'x',100.),(START,0,'y',50.),(START+40*60,0,'z',7.)]))
    first=process_event(event(1,0),RUN,store,lambda _:0.2)
    assert {name:first['features'][name] for name in RECEIVER_FEATURES}=={
        'in_txn_count_1h':0,'in_txn_count_24h':0,'in_txn_count_7d':1,'distinct_senders_24h':0,'distinct_senders_7d':1,
        'in_amount_sum_24h':0.,'in_amount_sum_7d':100.}
    # A second sender pays the same receiver 30 minutes later: sees the peer, the first replayed payment and x.
    second=process_event(event(1,30,index=2,account='0010'),RUN,store,lambda _:0.2)
    assert second['features']['in_txn_count_1h']==2 and second['features']['in_txn_count_7d']==3
    assert second['features']['distinct_senders_24h']==2 and second['features']['in_amount_sum_24h']==59.
    assert second['features']['txn_count_1h']==0
    receiver=store.states[RUN+':receiver:'+account_key('2','0002')]
    assert receiver['version']==2 and len(receiver['queues'][2])==4 and len(receiver['pending'])==1


def test_receiver_without_preload_fails_closed_and_commits_nothing():
    store=MemoryStore()
    with pytest.raises(ValueError,match='not preloaded'):process_event(event(),RUN,store,lambda _:0.2)
    assert store.states=={} and store.results=={}


def test_out_of_order_receiver_event_from_another_sender_rejected():
    store=MemoryStore(seeded());process_event(event(1,10),RUN,store,lambda _:0.2)
    with pytest.raises(ValueError,match='Out-of-order'):
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
    rows=pd.DataFrame([
        {'transaction_id':'HI-Small:1','timestamp':pd.Timestamp('2022-09-01 00:00'),'source_row_index':1,'from_bank':'001','from_account':'A','to_bank':'2','to_account':'R','amount_usd_asof':5.},
        {'transaction_id':'HI-Small:2','timestamp':pd.Timestamp('2022-09-01 01:00'),'source_row_index':2,'from_bank':'5','from_account':'B','to_bank':'02','to_account':'R','amount_usd_asof':7.5},
        {'transaction_id':'HI-Small:3','timestamp':pd.Timestamp('2022-09-02 00:00'),'source_row_index':3,'from_bank':'5','from_account':'B','to_bank':'3','to_account':'S','amount_usd_asof':1.}])
    seeds=receiver_seeds(rows,{'HI-Small:1'})
    assert list(seeds)==[account_key('2','R')]
    assert seeds[account_key('2','R')]=={'version':0,**empty_scan(),'pending':[[1661994000,2,account_key('5','B'),7.5]]}
