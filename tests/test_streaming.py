import base64,copy,json
from datetime import datetime,timedelta

import pytest
from bda_pipeline.streaming import MemoryStore,account_key,canonical,handle_records,process_event,transition

RUN='stream-smoke-20261006'

def event(ordinal=1,minute=0,index=None,bank='001',account='0009',target='0002'):
    index=index if index is not None else ordinal
    return {'run_id':RUN,'ordinal':ordinal,'transaction':{'transaction_id':'HI-Small:'+str(index),'source_row_index':index,
            'timestamp':(datetime(2022,9,1)+timedelta(minutes=minute)).isoformat(),'from_bank':bank,'from_account':account,
            'to_bank':'2','to_account':target,'amount_usd':10.,'amount_usd_asof':9.,'payment_format':'ACH',
            'payment_currency':'Euro','receiving_currency':'Euro'}}


def test_composite_identity_normalizes_bank_only():
    assert account_key('001','0009')==account_key('1','0009')
    assert account_key('1','0009')!=account_key('2','0009')
    assert account_key('1','0009')!=account_key('1','9')


def test_peer_exclusion_across_batches_and_window_boundaries():
    store=MemoryStore()
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
    store=BrokenAudit()
    with pytest.raises(RuntimeError): process_event(event(),RUN,store,lambda _:0.3)
    result=process_event(event(),RUN,store,lambda _:pytest.fail('Duplicate must not predict'))
    assert result['model_score']==0.3 and next(iter(store.states.values()))['version']==1
    changed=event();changed['transaction']['amount_usd_asof']=11
    with pytest.raises(ValueError,match='changed payload'):process_event(changed,RUN,store,lambda _:0.3)


@pytest.mark.parametrize('bad',[event(2),event(1,-1)])
def test_missing_or_out_of_order_event_rejected(bad):
    with pytest.raises(ValueError):process_event(bad,RUN,MemoryStore(),lambda _:0.3)


def test_older_timestamp_and_state_overflow_fail_closed():
    store=MemoryStore();process_event(event(1,2),RUN,store,lambda _:0.3)
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
    store=MemoryStore()
    response=handle_records({'Records':records},RUN,store,lambda _:0.3)
    assert response=={'batchItemFailures':[{'itemIdentifier':'101'}]}
    assert len(store.results)==1
