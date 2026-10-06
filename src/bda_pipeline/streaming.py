"""Bounded historical replay with exact batch-window semantics."""
import copy
import hashlib
import json
import re
from datetime import datetime

from .batch import FEATURES
from .inference import SCORING_COLUMNS

INPUT_COLUMNS = [column for column in SCORING_COLUMNS if column not in FEATURES]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def account_key(bank, account):
    return fingerprint([bank.lstrip('0') or '0', account])


def validate_event(event, run_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,49}', run_id) or event.get('run_id') != run_id:
        raise ValueError('Replay namespace mismatch')
    if set(event) != {'run_id', 'ordinal', 'transaction'} or set(event['transaction']) != set(INPUT_COLUMNS):
        raise ValueError('Unexpected input; labels and precomputed rolling features are prohibited')
    if type(event['ordinal']) is not int or event['ordinal'] < 1:
        raise ValueError('Invalid sender ordinal')
    txn = event['transaction']
    for name in ['transaction_id','from_bank','from_account','to_bank','to_account','payment_format','payment_currency','receiving_currency']:
        if not isinstance(txn[name],str) or not txn[name]:
            raise ValueError('Invalid identifier/category')
    if type(txn['source_row_index']) is not int or txn['source_row_index'] < 0 or txn['transaction_id'] != 'HI-Small:' + str(txn['source_row_index']):
        raise ValueError('Invalid source identity')
    for name in ['amount_usd','amount_usd_asof']:
        value=txn[name]
        if type(value) not in (int,float) or value < 0:
            raise ValueError('Invalid FX-normalized amount')
    canonical(event)  # Reject NaN, infinity and non-JSON values.
    now=datetime.fromisoformat(txn['timestamp'])
    if now.tzinfo is not None or not datetime(2022,9,1) <= now < datetime(2022,9,19):
        raise ValueError('Timestamp must use the dataset calendar convention')
    return int((now-datetime(1970,1,1)).total_seconds())


def transition(state, event, run_id):
    now=validate_event(event,run_id)
    state=state or {'version':0,'last_time':-1,'history':[]}
    if event['ordinal'] != state['version']+1 or now < state['last_time']:
        raise ValueError('Missing or out-of-order sender event; stop rather than score incomplete history')
    history=[entry for entry in state['history'] if entry[0] >= now-604800]
    windows=[[entry for entry in history if now-seconds <= entry[0] < now] for seconds in [3600,86400,604800]]
    features=dict(zip(FEATURES,[len(window) for window in windows]+[len({entry[1] for entry in windows[1]}),len({entry[1] for entry in windows[2]})]))
    txn=event['transaction']
    history.append([now,account_key(txn['to_bank'],txn['to_account'])])
    updated={'version':event['ordinal'],'last_time':now,'history':history}
    if len(history)>1000 or len(canonical(updated).encode())>128*1024:
        raise ValueError('Bounded demo state capacity exceeded; no silent truncation')
    return updated,features


class MemoryStore:
    """Local transactional stand-in used for failure/replay acceptance tests."""
    def __init__(self):
        self.states={}; self.results={}; self.audits={}

    def result(self,key):
        return copy.deepcopy(self.results.get(key))

    def state(self,key):
        return copy.deepcopy(self.states.get(key))

    def commit(self,key,old_version,state,result_key,result):
        if result_key in self.results or self.states.get(key,{}).get('version',0)!=old_version:
            raise ValueError('Concurrent commit; retry from durable result/state')
        self.states[key]=copy.deepcopy(state);self.results[result_key]=copy.deepcopy(result)

    def audit(self,key,result):
        self.audits[key]=copy.deepcopy(result)


def process_event(event,run_id,store,predict):
    validate_event(event,run_id)
    txn=event['transaction']
    result_key=run_id+':result:'+fingerprint(txn['transaction_id'])
    digest=fingerprint(event)
    result=store.result(result_key)
    if result is None:
        sender_key=run_id+':sender:'+account_key(txn['from_bank'],txn['from_account'])
        state=store.state(sender_key)
        updated,features=transition(state,event,run_id)
        score=float(predict({**txn,**features}))
        if not 0<=score<=1:
            raise ValueError('Invalid model ranking score')
        result={'input_sha256':digest,'transaction_id':txn['transaction_id'],'source_row_index':txn['source_row_index'],
                'timestamp':txn['timestamp'],'features':features,'model_score':score,'run_id':run_id,
                'score_meaning':'ranking-score-not-calibrated-probability'}
        store.commit(sender_key,(state or {}).get('version',0),updated,result_key,result)
    elif result['input_sha256']!=digest:
        raise ValueError('Duplicate identity has changed payload')
    # Durable result survives an S3 failure; redelivery repairs the audit without updating state twice.
    store.audit(result_key,result)
    return result


def handle_records(event,run_id,store,predict):
    import base64
    for record in event['Records']:
        try:
            payload=json.loads(base64.b64decode(record['kinesis']['data'],validate=True))
            expected_key=account_key(payload['transaction']['from_bank'],payload['transaction']['from_account'])
            if record['kinesis']['partitionKey']!=expected_key:
                raise ValueError('Composite partition-key mismatch')
            process_event(payload,run_id,store,predict)
        except Exception as error:
            print(canonical({'stream_record_failed':1,'sequence_number':record['kinesis']['sequenceNumber'],
                             'error_type':type(error).__name__}))
            # Stop the suffix: Lambda retries from the lowest failed sequence number.
            # No input/credential logging; the failure destination retains original records.
            return {'batchItemFailures':[{'itemIdentifier':record['kinesis']['sequenceNumber']}]}
    return {'batchItemFailures':[]}
