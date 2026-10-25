"""Bounded historical replay with exact batch-window semantics.

Where this sits in the pipeline
-------------------------------
This is the optional streaming demo: historical transactions are replayed
into a Kinesis Data Stream, a container-image Lambda (``streaming/handler.py``)
reads them, computes the sender's behaviour features on the fly, scores the
transaction with the frozen XGBoost model, and stores state/results in
DynamoDB (with an encrypted audit copy in S3).

This module holds the cloud-independent logic so it can be tested locally:

* ``validate_event``  strict schema / value checks for one replayed event.
* ``transition``      update one sender's rolling history and compute the
                      same 5 behaviour features as the batch path
                      (``features.behaviour.add_behaviour``), i.e. counts over the
                      half-open window [t - window, t), with transactions at
                      exactly the same timestamp excluded.
* ``MemoryStore``     in-memory stand-in for DynamoDB used by local tests and
                      ``scripts/streaming/prepare-streaming-demo.py``.
* ``process_event``   idempotent "score once" logic for one event.
* ``handle_records``  Lambda batch loop over Kinesis records with partial
                      batch failure reporting.

Event shape (JSON, base64-encoded inside the Kinesis record)::

    {"run_id": "<replay id>", "ordinal": <1, 2, 3 ... per sender>,
     "transaction": {<INPUT_COLUMNS only>}}

Labels and precomputed rolling features are rejected: the stream must derive
features itself, exactly as a live system would.
"""
import copy
import hashlib
import json
import re
from datetime import datetime

from ..features.behaviour import FEATURES
from .inference import SCORING_COLUMNS

# Raw fields an event must carry: the model's scoring columns minus the five
# behaviour features (those are computed here from the sender's history).
INPUT_COLUMNS = [column for column in SCORING_COLUMNS if column not in FEATURES]


def canonical(value):
    """Serialise ``value`` to one deterministic compact JSON string.

    Sorted keys and fixed separators mean equal data always gives identical
    text (needed for hashing). ``allow_nan=False`` makes NaN/Infinity raise.
    """
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    """Return the hex SHA-256 of ``canonical(value)``; used as a stable key/digest."""
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def account_key(bank, account):
    """Hashed identity of a (bank, account) pair.

    Leading zeros are stripped from the bank code (an all-zero code becomes
    '0'), matching ``features.behaviour.bank_key`` in the batch path, so '012' and '12'
    are the same bank. Hashing also avoids putting raw account numbers in
    DynamoDB keys and Kinesis partition keys.
    """
    return fingerprint([bank.lstrip('0') or '0', account])


def validate_event(event, run_id):
    """Check one replay event and return its timestamp as epoch seconds.

    Args:
        event: decoded event dict (see module docstring).
        run_id: the replay run this Lambda is configured for.

    Returns:
        int seconds since 1970-01-01 of the transaction timestamp (treated as
        naive dataset time, the same convention as the batch data).

    Raises:
        ValueError: if the run ID is unsafe or different, there are extra or
        missing fields (including labels/features), the ordinal is not a
        positive int, an ID/category is empty, the ID does not match
        ``'HI-Small:<source_row_index>'``, an amount is negative/non-numeric,
        a value is not valid JSON, or the timestamp has a timezone or falls
        outside 2022-09-01 .. 2022-09-18.
    """
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,49}', run_id) or event.get('run_id') != run_id:
        raise ValueError('Replay namespace mismatch')
    # Exact key sets: anything extra (e.g. is_laundering) is rejected.
    if set(event) != {'run_id', 'ordinal', 'transaction'} or set(event['transaction']) != set(INPUT_COLUMNS):
        raise ValueError('Unexpected input; labels and precomputed rolling features are prohibited')
    # type(...) is int (not isinstance) so that True/False are not accepted.
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
    """Advance one sender's state by one event and compute its features.

    Args:
        state: the sender's stored state, or None for a first event. Shape:
            ``{'version': n, 'last_time': epoch_s, 'history': [[epoch_s,
            receiver_key], ...]}`` where version = number of events applied.
        event: validated replay event for this sender.
        run_id: replay run ID.

    Returns:
        (updated_state, features): the new state to store and a dict of the
        five FEATURES for this transaction.

    Raises:
        ValueError: if an event for this sender is missing or out of order
        (ordinal must be exactly version+1 and time must not go backwards),
        or the state would exceed the demo's size bounds.

    Ordering rule: the features depend on the full previous history, so a
    gap would produce wrong features. The function stops instead of scoring.
    """
    now=validate_event(event,run_id)
    state=state or {'version':0,'last_time':-1,'history':[]}
    if event['ordinal'] != state['version']+1 or now < state['last_time']:
        raise ValueError('Missing or out-of-order sender event; stop rather than score incomplete history')
    # Keep only the last 7 days (604,800 s): older entries can never fall in
    # any window again, which keeps the state small.
    history=[entry for entry in state['history'] if entry[0] >= now-604800]
    # Windows of 1 hour, 24 hours and 7 days, each half-open [now-w, now):
    # earlier transactions at exactly the same second are excluded, which
    # matches the batch feature definition.
    windows=[[entry for entry in history if now-seconds <= entry[0] < now] for seconds in [3600,86400,604800]]
    # Features in FEATURES order: counts for 1h/24h/7d, then the number of
    # distinct receivers in the 24h and 7d windows.
    features=dict(zip(FEATURES,[len(window) for window in windows]+[len({entry[1] for entry in windows[1]}),len({entry[1] for entry in windows[2]})]))
    txn=event['transaction']
    # Add this transaction AFTER computing features (it must not count itself).
    history.append([now,account_key(txn['to_bank'],txn['to_account'])])
    updated={'version':event['ordinal'],'last_time':now,'history':history}
    # Bounds keep the item well under DynamoDB's 400 KB item limit; failing
    # loudly is safer than silently dropping history.
    if len(history)>1000 or len(canonical(updated).encode())>128*1024:
        raise ValueError('Bounded demo state capacity exceeded; no silent truncation')
    return updated,features


class MemoryStore:
    """Local transactional stand-in used for failure/replay acceptance tests.

    Mirrors the interface of ``streaming/handler.py``'s ``DynamoStore``:
    ``result``/``state`` reads, an all-or-nothing ``commit`` with an
    optimistic version check, and ``audit``. Deep copies stop callers from
    mutating stored data by accident.
    """
    def __init__(self):
        """Create empty dicts for sender states, results and audit copies."""
        self.states={}; self.results={}; self.audits={}

    def result(self,key):
        """Return a copy of the stored score result for ``key`` (or None)."""
        return copy.deepcopy(self.results.get(key))

    def state(self,key):
        """Return a copy of the stored sender state for ``key`` (or None)."""
        return copy.deepcopy(self.states.get(key))

    def commit(self,key,old_version,state,result_key,result):
        """Atomically save the new sender state and the result.

        Fails if the result already exists (already scored) or the sender's
        stored version is no longer ``old_version`` (someone else updated it
        in between), like the DynamoDB conditional transaction.
        """
        if result_key in self.results or self.states.get(key,{}).get('version',0)!=old_version:
            raise ValueError('Concurrent commit; retry from durable result/state')
        self.states[key]=copy.deepcopy(state);self.results[result_key]=copy.deepcopy(result)

    def audit(self,key,result):
        """Record an audit copy (S3 in the AWS version); overwriting is harmless."""
        self.audits[key]=copy.deepcopy(result)


def process_event(event,run_id,store,predict):
    """Score one event exactly once, even if Kinesis delivers it again.

    Args:
        event: decoded replay event.
        run_id: replay run ID (also prefixes every store key).
        store: MemoryStore or DynamoStore.
        predict: function(transaction dict with features) -> model score.

    Returns:
        The stored result dict (features, score, identity and input hash).

    Side effects: commits sender state + result once, then writes the audit.

    Idempotency: the result key is derived from the transaction ID. If a
    result already exists, the stored one is returned (state is not updated a
    second time), but only if the payload hash is identical; a different
    payload under the same ID is treated as an error.
    """
    validate_event(event,run_id)
    txn=event['transaction']
    result_key=run_id+':result:'+fingerprint(txn['transaction_id'])
    digest=fingerprint(event)
    result=store.result(result_key)
    if result is None:
        # First delivery: load the sender's state, compute features, score.
        sender_key=run_id+':sender:'+account_key(txn['from_bank'],txn['from_account'])
        state=store.state(sender_key)
        updated,features=transition(state,event,run_id)
        score=float(predict({**txn,**features}))
        if not 0<=score<=1:
            raise ValueError('Invalid model ranking score')
        result={'input_sha256':digest,'transaction_id':txn['transaction_id'],'source_row_index':txn['source_row_index'],
                'timestamp':txn['timestamp'],'features':features,'model_score':score,'run_id':run_id,
                'score_meaning':'ranking-score-not-calibrated-probability'}
        # State and result are saved together, conditional on the version
        # read above, so a crash cannot leave one without the other.
        store.commit(sender_key,(state or {}).get('version',0),updated,result_key,result)
    elif result['input_sha256']!=digest:
        raise ValueError('Duplicate identity has changed payload')
    # Durable result survives an S3 failure; redelivery repairs the audit without updating state twice.
    store.audit(result_key,result)
    return result


def handle_records(event,run_id,store,predict):
    """Process a Lambda batch of Kinesis records in order.

    Args:
        event: the Lambda event (``{'Records': [{'kinesis': {...}}, ...]}``).
        run_id, store, predict: passed through to ``process_event``.

    Returns:
        ``{'batchItemFailures': [...]}``, the response format for Lambda's
        ``ReportBatchItemFailures`` setting: empty on success, otherwise the
        sequence number of the first failing record.

    Each record's partition key must equal the sender's ``account_key``.
    Using the sender as partition key keeps one sender's events on one shard
    in order, which ``transition`` relies on.
    """
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
            # No input/credential logging; the failure destination retains original records.
            return {'batchItemFailures':[{'itemIdentifier':record['kinesis']['sequenceNumber']}]}
    return {'batchItemFailures':[]}
