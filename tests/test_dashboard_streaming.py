"""Streaming dashboard publisher tests: fixtures only, no AWS calls or credentials.

Committed items are produced by the real consumer logic (``process_event`` with
``MemoryStore``) so the publisher is checked against the exact payload format
the streaming Lambda writes to DynamoDB.
"""
import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from botocore.exceptions import ClientError

from bda_pipeline import dashboard_streaming as publisher
from bda_pipeline import streaming
from bda_pipeline.streaming import MemoryStore, account_key, canonical, process_event

RUN = 'stream-smoke-20261006'
SNAPSHOT_KEY = f'dashboard/streaming/{RUN}/snapshot.json'
ENV = {'RUN_ID': RUN, 'STATE_TABLE': 'is459-g3-' + RUN, 'INPUT_BUCKET': 'pipeline-bucket',
       'INPUT_KEY': f'streaming/{RUN}/input/events.jsonl', 'SITE_BUCKET': 'site-bucket',
       'MANIFEST_SHA256': publisher.OCTOBER_MODEL, 'CONSUMER_ENABLED': 'true', 'ALARM_NAME': f'is459-g3-{RUN}-record-failures'}
# Two transactions tie on 0.9 to exercise the event-time tie policy.
SCORES = {'HI-Small:1': .2, 'HI-Small:2': .9, 'HI-Small:3': .9, 'HI-Small:4': .05, 'HI-Small:5': .6}
PUBLIC_FIELDS = {'rank', 'transaction_id', 'timestamp', 'scored_at', 'model_score', 'review_now', 'amount_usd_asof', 'payment_format',
                 'payment_currency', 'receiving_currency', 'from_bank', 'from_account', 'to_bank', 'to_account',
                 *publisher.SENDER_FEATURES}
# The delta1 model's replay: all 15 behaviour features, receivers preloaded.
V2 = 'stream-delta1-20261107'
V2_FIELDS = PUBLIC_FIELDS | set(publisher.ALL_FEATURES)
RECEIVER = account_key('030', '80012ABCD')
# Wall-clock time of the first audit write; each later commit is 5 s apart.
COMMIT_START = datetime(2026, 11, 5, 10, 0, tzinfo=timezone.utc)


def replay(run=RUN):
    """Two complete sender histories, five events in replay (event-time) order, all paying one receiver."""
    rows = [('001', '8000EBD30', 0), ('002', '8000F4580', 5), ('001', '8000EBD30', 10),
            ('002', '8000F4580', 15), ('001', '8000EBD30', 20)]
    ordinals, events = {}, []
    for index, (bank, account, minute) in enumerate(rows, start=1):
        sender = account_key(bank, account)
        ordinals[sender] = ordinals.get(sender, 0) + 1
        events.append({'run_id': run, 'ordinal': ordinals[sender], 'transaction': {
            'transaction_id': f'HI-Small:{index}', 'source_row_index': index,
            'timestamp': (datetime(2022, 9, 1) + timedelta(minutes=minute)).isoformat(),
            'from_bank': bank, 'from_account': account, 'to_bank': '030', 'to_account': '80012ABCD',
            'amount_usd': 100. * index, 'amount_usd_asof': 99. * index, 'payment_format': 'ACH',
            'payment_currency': 'US Dollar', 'receiving_currency': 'US Dollar'}})
    return events


def stored_items(store):
    """The consumer's DynamoDB items: canonical payloads, sender versions as Decimal."""
    return ([{'pk': key, 'payload': canonical(value)} for key, value in store.results.items()] +
            [{'pk': key, 'version': Decimal(value['version']), 'payload': canonical(value)}
             for key, value in store.states.items()])


class FakeDynamo:
    def __init__(self, page=2):
        self.items, self.page, self.requests = [], page, []

    def scan(self, **request):
        self.requests.append(request)
        start = int(request.get('ExclusiveStartKey', {}).get('pk', 0))
        chunk = self.items[start:start + self.page]
        prefix = request['ExpressionAttributeValues'][':run']
        page = {'Items': [item for item in chunk if item['pk'].startswith(prefix)]}
        if start + self.page < len(self.items):
            page['LastEvaluatedKey'] = {'pk': str(start + self.page)}
        return page


class FakeS3:
    def __init__(self, objects):
        self.objects, self.puts, self.audits, self.listings = dict(objects), [], {}, []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken='0'):
        """Two audit objects per page, so the publisher must follow continuation tokens."""
        self.listings.append((Bucket, Prefix))
        keys = sorted(key for key in self.audits if key.startswith(Prefix))
        start = int(ContinuationToken)
        page = {'Contents': [{'Key': key, 'LastModified': self.audits[key]} for key in keys[start:start + 2]],
                'IsTruncated': start + 2 < len(keys)}
        if page['IsTruncated']:
            page['NextContinuationToken'] = str(start + 2)
        return page

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({'Error': {'Code': 'AccessDenied'}}, 'GetObject')
        return {'Body': BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, **request):
        self.puts.append(request)
        self.objects[(request['Bucket'], request['Key'])] = request['Body']


class FakeCloudWatch:
    def __init__(self):
        self.state = 'OK'

    def describe_alarms(self, AlarmNames):
        return {'MetricAlarms': [{'AlarmName': AlarmNames[0], 'StateValue': self.state}]}


def environment(monkeypatch, run, manifest):
    events = replay(run)
    raw = ('\n'.join(canonical(event) for event in events) + '\n').encode()
    key = f'streaming/{run}/input/events.jsonl'
    fake = {'dynamodb': FakeDynamo(), 's3': FakeS3({('pipeline-bucket', key): raw}), 'cloudwatch': FakeCloudWatch()}
    monkeypatch.setattr(publisher, 'clients', lambda: fake)
    publisher.reviewed_events.cache_clear()
    # The replay setup preloads the receiver before any payment arrives (no other incoming payments here).
    store = MemoryStore({f'{run}:receiver:{RECEIVER}': {'version': 0, **streaming.empty_scan(), 'pending': []}})
    env = {**ENV, 'RUN_ID': run, 'STATE_TABLE': 'is459-g3-' + run, 'INPUT_KEY': key, 'MANIFEST_SHA256': manifest,
           'ALARM_NAME': f'is459-g3-{run}-record-failures', 'INPUT_SHA256': hashlib.sha256(raw).hexdigest()}
    return SimpleNamespace(run=run, events=events, store=store, env=env, **fake)


@pytest.fixture
def aws(monkeypatch):
    """The 6 October run: its consumer committed sender features only and kept no receiver state."""
    yield environment(monkeypatch, RUN, publisher.OCTOBER_MODEL)
    publisher.reviewed_events.cache_clear()


@pytest.fixture
def aws2(monkeypatch):
    yield environment(monkeypatch, V2, publisher.DELTA1_MODEL)
    publisher.reviewed_events.cache_clear()


def consume(aws, events):
    """Score events with the consumer's own logic; expose its items to the scan and its audit writes to the listing."""
    for event in events:
        process_event(event, aws.run, aws.store, lambda txn: SCORES[txn['transaction_id']])
    for result_key in aws.store.audits:
        key = f'streaming/{aws.run}/audit/{streaming.fingerprint(result_key)}.json'
        aws.s3.audits.setdefault(key, COMMIT_START + timedelta(seconds=5 * len(aws.s3.audits)))
    aws.dynamodb.items = stored_items(aws.store)
    if aws.env['MANIFEST_SHA256'] == publisher.OCTOBER_MODEL:
        # What the 6 October consumer wrote: sender features only, no receiver items.
        aws.dynamodb.items = [october(item) for item in aws.dynamodb.items if ':receiver:' not in item['pk']]


def october(item):
    if ':result:' not in item['pk']:
        return item
    result = json.loads(item['payload'])
    result['features'] = {name: result['features'][name] for name in publisher.SENDER_FEATURES}
    return {**item, 'payload': canonical(result)}


def published(aws, now='2026-11-05T10:00:00Z'):
    summary = publisher.publish(aws.env, now=now)
    return summary, json.loads(aws.s3.puts[-1]['Body'])


def test_running_snapshot_ranks_committed_scores_without_private_fields(aws):
    consume(aws, aws.events[:3])
    summary, data = published(aws)
    assert summary == {'state': 'running', 'processed': 3, 'expected': 5}
    put = aws.s3.puts[-1]
    assert (put['Bucket'], put['Key']) == ('site-bucket', SNAPSHOT_KEY)
    assert put['CacheControl'] == 'no-store,max-age=0' and put['ServerSideEncryption'] == 'AES256'
    # Score descending; the 0.9 tie resolves by event time.
    assert [(row['rank'], row['transaction_id']) for row in data['queue']] == [
        (1, 'HI-Small:2'), (2, 'HI-Small:3'), (3, 'HI-Small:1')]
    assert data['latest'][0]['transaction_id'] == 'HI-Small:3'
    assert data['replay_position'] == aws.events[2]['transaction']['timestamp']
    assert data['senders'] == {'expected': 2, 'started': 2, 'complete': 0}
    assert data['max_score'] == .9 and data['reconciled'] is False
    assert all(set(row) == PUBLIC_FIELDS for row in data['queue'] + data['latest'])
    assert data['queue'][0]['from_account'] == '••••4580' and data['queue'][0]['txn_count_1h'] == 0
    body = put['Body'].decode()
    for private in ['8000EBD30', '8000F4580', '80012ABCD', 'input_sha256', 'is_laundering', ':sender:', ':receiver:']:
        assert private not in body
    # Strongly consistent and fully paginated.
    assert len(aws.dynamodb.requests) > 1 and all(request['ConsistentRead'] for request in aws.dynamodb.requests)


def test_completed_only_after_every_event_and_sender_history_reconciles(aws):
    consume(aws, aws.events)
    summary, data = published(aws)
    assert summary['state'] == 'completed' and data['reconciled'] is True
    assert data['senders'] == {'expected': 2, 'started': 2, 'complete': 2}
    assert publisher.run_state(5, 5, {'a': 3, 'b': 2}, {'a': 2, 'b': 2}, False, 'OK') == ('paused', False)


def test_review_flag_uses_the_batch_cutoff_over_every_committed_score(aws, monkeypatch):
    consume(aws, aws.events)
    data = published(aws)[1]
    # Fixture scores top out at 0.9, below the batch top-100 cutoff.
    assert data['review'] == {**publisher.REVIEWS[publisher.OCTOBER_MODEL], 'flagged': 0}
    assert not any(row['review_now'] for row in data['queue'] + data['latest'])
    monkeypatch.setitem(publisher.REVIEWS[publisher.OCTOBER_MODEL], 'cutoff', .6)
    data = published(aws)[1]
    # Inclusive cutoff, counted over all committed scores rather than the shown rows.
    assert data['review']['flagged'] == 3
    assert {row['transaction_id'] for row in data['queue'] if row['review_now']} == {'HI-Small:2', 'HI-Small:3', 'HI-Small:5'}


def test_batch_parity_is_reported_only_for_a_run_with_recorded_acceptance(aws):
    consume(aws, aws.events[:2])
    assert published(aws)[1]['verification'] == publisher.VERIFIED[RUN]
    assert publisher.VERIFIED.get('another-run') is None


def test_a_model_without_records_gets_neither_the_cutoff_nor_the_parity_record(aws, monkeypatch):
    consume(aws, aws.events)
    monkeypatch.delitem(publisher.REVIEWS, publisher.OCTOBER_MODEL)
    monkeypatch.setitem(publisher.VERIFIED[RUN], 'model_manifest_sha256', publisher.DELTA1_MODEL)
    data = published(aws)[1]
    assert data['review'] is None and data['verification'] is None
    assert all(row['review_now'] is None for row in data['queue'] + data['latest'])
    assert data['state'] == 'completed' and data['processed'] == 5


def test_unknown_model_or_mismatched_features_publish_nothing(aws):
    consume(aws, aws.events)
    with pytest.raises(ValueError, match='Unsupported'):
        publisher.publish({**aws.env, 'MANIFEST_SHA256': 'e' * 64})
    # Sender-only results cannot be served as the delta1 model.
    with pytest.raises(ValueError, match='does not match'):
        publisher.publish({**aws.env, 'MANIFEST_SHA256': publisher.DELTA1_MODEL})
    assert aws.s3.puts == []


def test_november_run_carries_receiver_features_and_reconciles_receivers(aws2):
    consume(aws2, aws2.events[:3])
    summary, data = published(aws2)
    assert summary == {'state': 'running', 'processed': 3, 'expected': 5}
    assert all(set(row) == V2_FIELDS for row in data['queue'] + data['latest'])
    # Two earlier payments into the shared receiver, from two distinct senders, inside 24 hours.
    assert data['latest'][0]['in_txn_count_24h'] == 2 and data['latest'][0]['distinct_senders_24h'] == 2
    assert data['receivers'] == {'expected': 1, 'preloaded': 1, 'complete': 0}
    # Fixture scores top out at 0.9, below delta1's batch top-100 cutoff (0.9954).
    assert data['review'] == {**publisher.REVIEWS[publisher.DELTA1_MODEL], 'flagged': 0}
    assert isinstance(data['latest'][0]['amount_sum_7d'], float) and data['latest'][0]['corridor_prior_count'] == 1
    assert data['verification'] is None and 'preloaded' in data['cohort']
    consume(aws2, aws2.events)
    summary, data = published(aws2)
    assert summary['state'] == 'completed' and data['reconciled'] is True
    assert data['receivers'] == {'expected': 1, 'preloaded': 1, 'complete': 1}
    assert ':receiver:' not in aws2.s3.puts[-1]['Body'].decode()


def test_november_run_without_preloaded_receivers_never_completes(aws2):
    consume(aws2, aws2.events)
    aws2.dynamodb.items = [item for item in aws2.dynamodb.items if ':receiver:' not in item['pk']]
    summary, data = published(aws2)
    assert summary['state'] == 'running' and data['reconciled'] is False
    assert data['receivers'] == {'expected': 1, 'preloaded': 0, 'complete': 0}


def test_november_run_rejects_unknown_receivers_and_sender_only_results(aws2):
    consume(aws2, aws2.events[:2])
    aws2.dynamodb.items.append({'pk': f'{V2}:receiver:{"f" * 64}', 'version': Decimal(0), 'payload': '{}'})
    with pytest.raises(ValueError, match='Unknown receiver'):
        publisher.publish(aws2.env)
    aws2.dynamodb.items = [october(item) for item in aws2.dynamodb.items[:-1]]
    with pytest.raises(ValueError, match='does not match'):
        publisher.publish(aws2.env)
    assert aws2.s3.puts == []


def test_recorded_parity_matches_the_committed_evidence():
    path = Path(__file__).resolve().parents[1] / publisher.VERIFIED[RUN]['evidence']
    evidence = json.loads(path.read_text(encoding='utf-8'))
    recorded = publisher.VERIFIED[RUN]
    assert evidence['run_id'] == RUN and evidence['reconciliation']['transactions'] == recorded['transactions']
    assert evidence['reconciliation']['features_exact'] is recorded['features_exact'] is True
    assert evidence['reconciliation']['max_absolute_score_difference'] == recorded['max_absolute_score_difference']


def test_waiting_paused_and_failed_states(aws):
    assert published(aws)[0]['state'] == 'waiting'
    consume(aws, aws.events[:2])
    aws.env['CONSUMER_ENABLED'] = 'false'
    assert published(aws)[0]['state'] == 'paused'
    aws.cloudwatch.state = 'ALARM'
    assert published(aws)[0]['state'] == 'failed'


def test_duplicate_delivery_leaves_queue_and_counts_unchanged(aws):
    consume(aws, aws.events)
    first = published(aws)[1]
    consume(aws, aws.events)
    aws.dynamodb.items = aws.dynamodb.items * 2
    second = published(aws)[1]
    assert second['queue'] == first['queue'] and second['processed'] == first['processed'] == 5


def tamper_score(item):
    item['payload'] = item['payload'].replace('"model_score":0.9', '"model_score":1.5')


def tamper_input(item):
    item['payload'] = item['payload'].replace('"input_sha256":"', '"input_sha256":"0')


def tamper_features(item):
    item['payload'] = item['payload'].replace('"features":{', '"features":{"in_txn_count_1h":0,')


def tamper_identity(item):
    item['payload'] = item['payload'].replace('HI-Small:2', 'HI-Small:99')


def tamper_type(item):
    item['pk'] = item['pk'].replace(':result:', ':other:')


@pytest.mark.parametrize('tamper', [tamper_score, tamper_input, tamper_features, tamper_identity, tamper_type])
def test_invalid_committed_item_keeps_last_good_snapshot(aws, tamper):
    consume(aws, aws.events[:2])
    published(aws)
    good = aws.s3.objects[('site-bucket', SNAPSHOT_KEY)]
    tamper(next(item for item in aws.dynamodb.items if 'HI-Small:2' in item.get('payload', '') and ':result:' in item['pk']))
    with pytest.raises(ValueError):
        publisher.publish(aws.env)
    assert len(aws.s3.puts) == 1 and aws.s3.objects[('site-bucket', SNAPSHOT_KEY)] == good


def test_over_cap_scan_and_modified_events_fail_before_writing(aws, monkeypatch):
    consume(aws, aws.events)
    monkeypatch.setattr(publisher, 'MAX_ITEMS', 4)
    with pytest.raises(ValueError, match='scan cap'):
        publisher.publish(aws.env)
    monkeypatch.setattr(publisher, 'MAX_ITEMS', 5000)
    with pytest.raises(ValueError, match='reviewed digest'):
        publisher.publish({**aws.env, 'INPUT_SHA256': '0' * 64})
    assert aws.s3.puts == []


def test_progress_curve_uses_each_scores_audit_commit_time(aws):
    consume(aws, aws.events[:3])
    data = published(aws, now='2026-11-05T11:00:00Z')[1]
    # One publication still shows the whole history, one point per committed score.
    assert data['progress'] == [{'at': '2026-11-05T10:00:00Z', 'processed': 1},
                                {'at': '2026-11-05T10:00:05Z', 'processed': 2},
                                {'at': '2026-11-05T10:00:10Z', 'processed': 3}]
    assert data['last_progress_at'] == '2026-11-05T10:00:10Z'
    assert data['latest'][0]['scored_at'] == '2026-11-05T10:00:10Z'
    assert aws.s3.listings[0] == ('pipeline-bucket', f'streaming/{RUN}/audit/') and len(aws.s3.listings) == 2


def test_progress_ends_at_committed_total_and_thins_long_histories(monkeypatch):
    start = COMMIT_START
    # An audit copy not written yet: the curve still ends at the DynamoDB total.
    assert publisher.progress([start], 2, 'now') == [{'at': '2026-11-05T10:00:00Z', 'processed': 1},
                                                     {'at': 'now', 'processed': 2}]
    assert publisher.progress([], 0, 'now') == [{'at': 'now', 'processed': 0}]
    monkeypatch.setattr(publisher, 'PROGRESS_POINTS', 3)
    points = publisher.progress([start + timedelta(seconds=n) for n in range(5)], 5, 'now')
    assert [point['processed'] for point in points] == [1, 3, 5]


def test_events_parity_with_consumer_hashes_and_windows_line_endings():
    events = replay()
    assert publisher.fingerprint(events[0]) == streaming.fingerprint(events[0])
    assert publisher.account_key('001', 'A') == streaming.account_key('001', 'A')
    raw = ('\r\n'.join(canonical(event) for event in events) + '\r\n').encode()
    parsed, senders = publisher.read_events(raw, RUN, hashlib.sha256(raw).hexdigest())
    assert len(parsed) == 5 and sorted(senders.values()) == [2, 3]
    with pytest.raises(ValueError, match='Unexpected replay event'):
        publisher.read_events(raw, 'another-run', hashlib.sha256(raw).hexdigest())


def test_handler_repeats_inside_the_scheduled_minute(monkeypatch):
    clock, calls = [0.0], []
    monkeypatch.setattr(publisher.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(publisher.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(publisher, 'publish', lambda env: calls.append(clock[0]) or {'state': 'running'})
    monkeypatch.setenv('PUBLISH_INTERVAL_SECONDS', '15')
    assert publisher.handler({}, None) == {'state': 'running'}
    assert calls == [0, 15, 30, 45]
    calls.clear()
    publisher.handler({'once': True}, None)
    assert len(calls) == 1


def test_publish_failure_surfaces_as_lambda_error(monkeypatch):
    def fail(env):
        raise ValueError('Committed result does not match the reviewed replay')
    monkeypatch.setattr(publisher, 'publish', fail)
    with pytest.raises(ValueError):
        publisher.handler({}, None)


def test_prepare_script_packages_only_the_publisher(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'scripts/aws/prepare-dashboard-streaming.py'
    spec = importlib.util.spec_from_file_location('prepare_dashboard_streaming', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.prepare(tmp_path / 'package')
    archive = tmp_path / 'package' / manifest['file']
    assert ZipFile(archive).namelist() == ['dashboard_streaming.py']
    assert manifest['sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='fresh'):
        module.prepare(tmp_path / 'package')
