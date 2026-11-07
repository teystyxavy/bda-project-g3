"""Bounded historical replay with batch-window semantics: Kinesis events are scored by a Lambda that keeps per-sender
and per-receiver state in DynamoDB.

Events are ``{"run_id", "ordinal" (per sender), "transaction": {INPUT_COLUMNS only}}``; labels and precomputed features
are rejected. Each event yields every batch behaviour feature (``STREAM_FEATURES`` = ``batch.FEATURES``): the seven
outgoing window features of its sender, the seven incoming ones of its receiver and the sender->receiver prior-pair count.

Exact batch parity. ``scan_step`` repeats the per-account scan of ``curate_transactions._windowed_counts`` one row at a
time: rows are applied in (event time, source row) order; when time moves forward, entries older than each window are
evicted (USD sums decremented and clamped at 0) and a snapshot is taken; rows sharing a timestamp reuse that snapshot,
so they exclude each other; the row is then added. Keeping the queues and running sums in state reproduces the batch
values bit for bit, floating-point sums included. The prior-pair count is the all-time number of earlier rows from the
sender to the same receiver in that order (``_prior_pair_counts``); same-timestamp rows earlier in source order count.

Receiver state. A receiver is paid by senders outside the replay too. ``receiver_seeds`` takes those payments from the
verified batch data and the replay setup stores them once (version 0) as the receiver's ``pending`` rows. When a
replayed payment arrives, pending rows before it are applied first, so the receiver's scan reaches exactly the point
batch had reached. Scoring a payment to a receiver without that preloaded state fails closed, as does any event at or
before the last position already applied. The demo stream has one shard and the consumer one concurrent batch, so
events are applied in send order; the producer sends in (event time, source row) order."""
import copy
import hashlib
import json
import re
from datetime import datetime

from .batch import FEATURES
from .inference import SCORING_COLUMNS

# Raw fields an event must carry: the scoring columns minus every behaviour feature.
INPUT_COLUMNS = [column for column in SCORING_COLUMNS if column not in FEATURES]
# Window feature names in scan order: counts 1h/24h/7d, distinct 24h/7d, USD sums 24h/7d.
SENDER_FEATURES = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
                   'distinct_counterparties_24h', 'distinct_counterparties_7d',
                   'amount_sum_24h', 'amount_sum_7d']
RECEIVER_FEATURES = ['in_txn_count_1h', 'in_txn_count_24h', 'in_txn_count_7d',
                     'distinct_senders_24h', 'distinct_senders_7d',
                     'in_amount_sum_24h', 'in_amount_sum_7d']
PAIR_FEATURE = 'corridor_prior_count'
# Every batch behaviour feature, in batch order.
STREAM_FEATURES = [name for name in FEATURES if name in {*SENDER_FEATURES, *RECEIVER_FEATURES, PAIR_FEATURE}]
assert STREAM_FEATURES == list(FEATURES), 'Streaming must compute every batch behaviour feature'
# Window lengths in seconds: 1 hour, 24 hours, 7 days.
WINDOWS = [3600, 86400, 604800]
# Bounds keep each state item well under DynamoDB's 400 KB item limit.
MAX_HISTORY = 2000
MAX_STATE_BYTES = 300*1024
# Replay sources: the curated identities '<subset>:<source row>' and the datasets' calendar.
SOURCE_ID = re.compile(r'HI-(Small|Medium|Large):([0-9]+)')
FIRST_DAY, END_DAY = datetime(2022, 9, 1), datetime(2023, 2, 1)
EPOCH = datetime(1970, 1, 1)


def canonical(value):
    """Deterministic compact JSON (sorted keys, no NaN) so equal data always hashes equally."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    """Return the hex SHA-256 of ``canonical(value)``; used as a stable key/digest."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def account_key(bank, account):
    """Hashed (bank, account) identity with leading zeros stripped from the bank, matching ``analytics.bank_key``."""
    return fingerprint([bank.lstrip('0') or '0', account])


def validate_event(event, run_id):
    """Checks one replay event and returns its timestamp as epoch seconds.
    Raises ValueError on any schema, value, ordinal or out-of-range timestamp problem."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,49}', run_id) or event.get('run_id') != run_id:
        raise ValueError('Replay namespace mismatch')
    if set(event) != {'run_id', 'ordinal', 'transaction'} or set(event['transaction']) != set(INPUT_COLUMNS):
        raise ValueError('Unexpected input; labels and precomputed rolling features are prohibited')
    # type(...) is int (not isinstance) so that True/False are not accepted.
    if type(event['ordinal']) is not int or event['ordinal'] < 1:
        raise ValueError('Invalid sender ordinal')
    txn = event['transaction']
    for name in ['transaction_id','from_bank','from_account','to_bank','to_account','payment_format','payment_currency','receiving_currency']:
        if not isinstance(txn[name],str) or not txn[name]:
            raise ValueError('Invalid identifier/category')
    identity = SOURCE_ID.fullmatch(txn['transaction_id'])
    if type(txn['source_row_index']) is not int or txn['source_row_index'] < 0 or identity is None or identity.group(2) != str(txn['source_row_index']):
        raise ValueError('Invalid source identity')
    for name in ['amount_usd','amount_usd_asof']:
        value=txn[name]
        if type(value) not in (int,float) or value < 0:
            raise ValueError('Invalid FX-normalized amount')
    canonical(event)  # Reject NaN, infinity and non-JSON values.
    now=datetime.fromisoformat(txn['timestamp'])
    if now.tzinfo is not None or not FIRST_DAY <= now < END_DAY:
        raise ValueError('Timestamp must use the dataset calendar convention')
    return int((now-EPOCH).total_seconds())


def empty_scan():
    """A fresh per-account scan: three window queues of [epoch, party, USD], their running USD sums, the current time
    and the snapshot taken at it, and the last applied (epoch, source row) position."""
    return {'queues':[[],[],[]],'sums':[0.0,0.0,0.0],'now':None,'snapshot':None,'last':None}


def scan_step(scan, epoch, row, party, amount):
    """Applies one row to a per-account scan in place, exactly as ``curate_transactions._windowed_counts``, and returns
    its seven window values (counts 1h/24h/7d, distinct 24h/7d, sums 24h/7d) from before the row was added.
    Raises ValueError unless (epoch, row) is after the last applied position."""
    if scan['last'] is not None and [epoch,row] <= scan['last']:
        raise ValueError('Out-of-order event; stop rather than score incomplete history')
    queues,sums=scan['queues'],scan['sums']
    if epoch != scan['now']:
        # Time moved forward: evict entries older than now - window (the cutoff itself stays), then snapshot.
        scan['now']=epoch
        for window,seconds in enumerate(WINDOWS):
            while queues[window] and queues[window][0][0] < epoch-seconds:
                _,_,value=queues[window].pop(0)
                # Clamp at the eviction point, as batch does, so float drift never compounds.
                sums[window]=max(0.0,sums[window]-value)
        scan['snapshot']=[len(queues[0]),len(queues[1]),len(queues[2]),
                          len({entry[1] for entry in queues[1]}),len({entry[1] for entry in queues[2]}),sums[1],sums[2]]
    values=list(scan['snapshot'])
    # Add the row AFTER taking its values (it must not count itself).
    for window in range(3):
        queues[window].append([epoch,party,amount])
        sums[window]+=amount
    scan['last']=[epoch,row]
    return values


def bounded(state):
    """Return ``state`` unless it exceeds the demo bounds; fail loudly rather than drop history."""
    if (len(state['queues'][2])+len(state.get('pending',[]))>MAX_HISTORY
            or len(canonical(state).encode())>MAX_STATE_BYTES):
        raise ValueError('Bounded demo state capacity exceeded; no silent truncation')
    return state


def transition(state, event, run_id):
    """Advances one sender's state by one event and returns (updated_state, features).
    Features are the seven outgoing window values plus the prior-pair count to this receiver.
    Raises ValueError if the event is missing or out of order (ordinal != version + 1, or not after the last applied
    position) or the state exceeds its size bounds."""
    now=validate_event(event,run_id)
    state=copy.deepcopy(state) if state else {'version':0,**empty_scan(),'pairs':{}}
    if event['ordinal'] != state['version']+1:
        raise ValueError('Missing or out-of-order sender event; stop rather than score incomplete history')
    txn=event['transaction']
    receiver=account_key(txn['to_bank'],txn['to_account'])
    values=scan_step(state,now,txn['source_row_index'],receiver,txn['amount_usd_asof'])
    features=dict(zip(SENDER_FEATURES,values))
    # All-time earlier payments from this sender to this receiver, counted before this one is added.
    features[PAIR_FEATURE]=state['pairs'].get(receiver,0)
    state['pairs'][receiver]=features[PAIR_FEATURE]+1
    state['version']=event['ordinal']
    return bounded(state),features


def receive(state, event, run_id):
    """Advances one receiver's preloaded state to one replayed payment and returns (updated_state, features).

    Pending preloaded payments (from senders outside the replay) that come before the event are applied first, then
    the event itself. Raises ValueError if the receiver was not preloaded, the event is not after the last applied
    position, or the state exceeds its size bounds."""
    now=validate_event(event,run_id)
    if state is None:
        raise ValueError('Receiver history was not preloaded; stop rather than undercount incoming activity')
    state=copy.deepcopy(state)
    txn=event['transaction']
    position=[now,txn['source_row_index']]
    pending=state['pending']
    while pending and pending[0][:2] < position:
        epoch,row,sender,amount=pending.pop(0)
        scan_step(state,epoch,row,sender,amount)
    values=scan_step(state,now,txn['source_row_index'],account_key(txn['from_bank'],txn['from_account']),txn['amount_usd_asof'])
    state['version']+=1
    return bounded(state),dict(zip(RECEIVER_FEATURES,values))


def receiver_seeds(frame, replayed):
    """Preloaded receiver states for one replay, from verified batch rows with ``transaction_id``, ``timestamp``,
    ``source_row_index``, bank/account columns and ``amount_usd_asof``.

    ``replayed`` is the set of ``transaction_id`` values in the replay. Returns {receiver key: state} for every receiver
    the replay pays; each holds, as ``pending`` [epoch, source row, sender key, USD] rows in scan order, that receiver's
    other incoming payments (the replayed ones arrive through the stream). ``frame`` must contain every transaction
    into those receivers, which the full batch dataset does."""
    targets={account_key(row.to_bank,row.to_account) for row in frame.itertuples(index=False) if row.transaction_id in replayed}
    seeds={key:{'version':0,**empty_scan(),'pending':[]} for key in targets}
    for row in frame.itertuples(index=False):
        key=account_key(row.to_bank,row.to_account)
        if key in seeds and row.transaction_id not in replayed:
            seeds[key]['pending'].append([int((row.timestamp.to_pydatetime()-EPOCH).total_seconds()),int(row.source_row_index),
                                          account_key(row.from_bank,row.from_account),float(row.amount_usd_asof)])
    for state in seeds.values():
        state['pending'].sort(key=lambda entry:entry[:2])
    return {key:bounded(state) for key,state in seeds.items()}


class MemoryStore:
    """In-memory stand-in for ``streaming/handler.py``'s ``DynamoStore``, with an atomic versioned ``commit``."""
    def __init__(self, states=None):
        """Create empty dicts for results and audit copies; ``states`` optionally preloads state items (receiver seeds)."""
        self.states=copy.deepcopy(states or {}); self.results={}; self.audits={}

    def result(self,key):
        """Return a copy of the stored score result for ``key`` (or None)."""
        return copy.deepcopy(self.results.get(key))

    def state(self,key):
        """Return a copy of the stored sender or receiver state for ``key`` (or None)."""
        return copy.deepcopy(self.states.get(key))

    def commit(self,updates,result_key,result):
        """Atomically saves state updates and the result.

        ``updates`` is a list of (key, old_version, state); ``old_version`` None means the item must not exist yet.
        Fails if the result exists or any state's version changed since it was read."""
        for key,old_version,_ in updates:
            current=self.states.get(key)
            if (current is not None) if old_version is None else (current is None or current['version']!=old_version):
                raise ValueError('Concurrent commit; retry from durable result/state')
        if result_key in self.results:
            raise ValueError('Concurrent commit; retry from durable result/state')
        for key,_,state in updates:
            self.states[key]=copy.deepcopy(state)
        self.results[result_key]=copy.deepcopy(result)

    def audit(self,key,result):
        """Record an audit copy (S3 in the AWS version); overwriting is harmless."""
        self.audits[key]=copy.deepcopy(result)


def process_event(event,run_id,store,predict):
    """Scores one event exactly once: a replay returns the stored result unless its payload hash differs (an error).
    Commits sender state, receiver state and result together, then writes the audit copy."""
    validate_event(event,run_id)
    txn=event['transaction']
    result_key=run_id+':result:'+fingerprint(txn['transaction_id'])
    digest=fingerprint(event)
    result=store.result(result_key)
    if result is None:
        sender_key=run_id+':sender:'+account_key(txn['from_bank'],txn['from_account'])
        receiver_key=run_id+':receiver:'+account_key(txn['to_bank'],txn['to_account'])
        state,inbound=store.state(sender_key),store.state(receiver_key)
        updated,sender_features=transition(state,event,run_id)
        received,receiver_features=receive(inbound,event,run_id)
        features={**sender_features,**receiver_features}
        score=float(predict({**txn,**features}))
        if not 0<=score<=1:
            raise ValueError('Invalid model ranking score')
        result={'input_sha256':digest,'transaction_id':txn['transaction_id'],'source_row_index':txn['source_row_index'],
                'timestamp':txn['timestamp'],'features':features,'model_score':score,'run_id':run_id,
                'score_meaning':'ranking-score-not-calibrated-probability'}
        # Saved together, conditional on the versions read above, so a crash cannot leave one without the others.
        store.commit([(sender_key,None if state is None else state['version'],updated),
                      (receiver_key,inbound['version'],received)],result_key,result)
    elif result['input_sha256']!=digest:
        raise ValueError('Duplicate identity has changed payload')
    # Durable result survives an S3 failure; redelivery repairs the audit without updating state twice.
    store.audit(result_key,result)
    return result


def handle_records(event,run_id,store,predict):
    """Processes a Lambda batch of Kinesis records in order, returning ``batchItemFailures`` with the first failing sequence number.
    Each partition key must equal the sender's ``account_key`` so one sender's events stay ordered on one shard."""
    import base64
    for record in event['Records']:
        try:
            payload=json.loads(base64.b64decode(record['kinesis']['data'],validate=True))
            expected_key=account_key(payload['transaction']['from_bank'],payload['transaction']['from_account'])
            if record['kinesis']['partitionKey']!=expected_key:
                raise ValueError('Composite partition-key mismatch')
            process_event(payload,run_id,store,predict)
        except Exception as error:
            # Log only the sequence number and error class (no payload).
            print(canonical({'stream_record_failed':1,'sequence_number':record['kinesis']['sequenceNumber'],
                             'error_type':type(error).__name__}))
            # Stop the suffix: Lambda retries from the lowest failed sequence number.
            return {'batchItemFailures':[{'itemIdentifier':record['kinesis']['sequenceNumber']}]}
    return {'batchItemFailures':[]}
