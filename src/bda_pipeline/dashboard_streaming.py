"""Publish committed streaming scores as the dashboard's Incoming AML snapshot.

Where this sits in the pipeline
-------------------------------
A small scheduled Lambda beside the streaming consumer (``infra/streaming``)::

    Kinesis -> consumer Lambda -> DynamoDB (committed scores + sender state)
    DynamoDB (consistent scan) + reviewed replay events.jsonl (S3, pinned SHA-256)
        -> this Lambda -> s3://<dashboard bucket>/dashboard/streaming/<run>/snapshot.json
        -> CloudFront /streaming/<run>/snapshot.json -> Incoming AML view (polls every 10 s)

It never scores, never changes sender state and only lists the audit copies,
so a dashboard failure cannot affect predictions. Committed results hold
identity, event time, sender features and score only; display fields (amount,
payment format, currencies, banks) are joined from the reviewed replay events.
Wall-clock scoring times come from the audit copies' S3 write times: the
consumer writes each one right after its DynamoDB commit.

Each scored transaction is marked ``review_now`` when it reaches the batch
reviewer cutoff (``REVIEW``), and a run with recorded cloud acceptance carries
its batch-parity result (``VERIFIED``).

A snapshot is written only after every committed item validates against those
events. On any failure the previous snapshot stays in place and the browser
labels it stale. Account identifiers are masked; labels never exist here.
Scores are uncalibrated rankings, not laundering probabilities.

Environment variables (set by Terraform): ``RUN_ID``, ``STATE_TABLE``,
``INPUT_BUCKET`` (pipeline bucket: replay events and audit copies),
``INPUT_KEY``/``INPUT_SHA256`` (reviewed events.jsonl),
``SITE_BUCKET`` (dashboard bucket), ``MANIFEST_SHA256`` (frozen model, shown
for provenance), ``CONSUMER_ENABLED`` (``true`` while the Kinesis mapping is
enabled), ``ALARM_NAME`` (consumer record-failure alarm) and
``PUBLISH_INTERVAL_SECONDS``.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from botocore.config import Config

SCHEMA_VERSION = 1
TOP_K = 100
LATEST = 15
# Bounded demo: fail visibly rather than publish a partial queue.
MAX_ITEMS = 5000
MAX_EVENTS = 2000
MAX_BYTES = 1_000_000
PROGRESS_POINTS = 240
# EventBridge Scheduler fires once a minute; repeat inside that minute.
WINDOW_SECONDS = 50
SENDER_FEATURES = ['txn_count_1h', 'txn_count_24h', 'txn_count_7d',
                   'distinct_counterparties_24h', 'distinct_counterparties_7d']
SCORE_MEANING = 'ranking-score-not-calibrated-probability'
# Batch reviewer capacity for the same frozen model: the lowest score in the
# HI-Small validation top-100 queue (release opt-full-20261006-aws1). It was set
# on validation days, so it is a capacity, not an invented threshold; labels
# are never used here. Both records below hold only for this model manifest
# (model 'f0dc65ca…'), so a run serving another model publishes neither.
REVIEW = {'cutoff': 0.9937289953, 'capacity': 100, 'period': '2022-09-07 to 2022-09-08',
          'source_run': 'opt-full-20261006-aws1',
          'model_manifest_sha256': '323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f'}
# Recorded cloud acceptance per replay run: every committed score was compared
# with the frozen batch reference for the same transaction.
VERIFIED = {'stream-smoke-20261006': {
    'transactions': 884, 'features_exact': True, 'max_absolute_score_difference': 2.9103830456733704e-11,
    'verified_on': '2026-10-06', 'evidence': 'planning/evidence/streaming-deployment-result-20261006.json'}}


def canonical(value):
    """Same compact JSON as ``streaming.canonical``; that module needs pandas, absent from this zip."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def account_key(bank, account):
    """Hashed sender identity, matching ``streaming.account_key``."""
    return fingerprint([bank.lstrip('0') or '0', account])


def mask(account):
    """Last four characters only: enough to tell rows apart on screen."""
    return '••••' + account[-4:]


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


@lru_cache(maxsize=1)
def clients():
    config = Config(connect_timeout=3, read_timeout=8,
                    retries={'mode': 'standard', 'total_max_attempts': 2},
                    user_agent_appid='IS459-DashboardStreaming')
    session = boto3.Session()
    # Resource meta client marshals native values, as in streaming/handler.py.
    return {'dynamodb': session.resource('dynamodb', config=config).meta.client,
            's3': session.client('s3', config=config),
            'cloudwatch': session.client('cloudwatch', config=config)}


def read_events(raw, run_id, expected_sha256):
    """Parse the reviewed replay events after checking their pinned SHA-256.

    Returns ({transaction_id: event}, {sender_key: final ordinal}).
    """
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Replay events do not match the reviewed digest')
    events, senders = {}, {}
    for line in raw.decode('utf-8').splitlines():
        event = json.loads(line)
        txn = event['transaction']
        if event['run_id'] != run_id or txn['transaction_id'] in events:
            raise ValueError('Unexpected replay event')
        events[txn['transaction_id']] = event
        sender = account_key(txn['from_bank'], txn['from_account'])
        senders[sender] = max(senders.get(sender, 0), event['ordinal'])
    if not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError('Replay cohort outside the reviewed bound')
    return events, senders


@lru_cache(maxsize=1)
def reviewed_events(bucket, key, sha256, run_id):
    """Read once per warm container; the pinned digest makes the cache safe."""
    response = clients()['s3'].get_object(Bucket=bucket, Key=key)
    with response['Body'] as body:
        return read_events(body.read(), run_id, sha256)


def scan_items(ddb, table, run_id):
    """All items of this run through a strongly consistent, paginated, capped scan.

    The table is keyed only by ``pk``, so this is a bounded scan, not a query.
    During writes it is not a point-in-time view; completion is decided by
    reconciliation against the reviewed events, never by counts alone.
    """
    items, start = [], None
    while True:
        request = {'TableName': table, 'ConsistentRead': True,
                   'FilterExpression': 'begins_with(pk, :run)',
                   'ExpressionAttributeValues': {':run': run_id + ':'}}
        if start:
            request['ExclusiveStartKey'] = start
        page = ddb.scan(**request)
        items += page.get('Items', [])
        if len(items) > MAX_ITEMS:
            raise ValueError('Streaming state exceeds the reviewed scan cap')
        start = page.get('LastEvaluatedKey')
        if not start:
            return items


def collect(items, run_id, events, senders):
    """Check committed items against the reviewed events.

    Returns ({transaction_id: (result, event)}, {sender_key: version}).
    Raises ValueError on anything the consumer could not have written for
    this replay (unknown identity, changed input, invalid score or features).
    """
    rows, versions = {}, {}
    result_prefix, sender_prefix = run_id + ':result:', run_id + ':sender:'
    for item in items:
        key = item['pk']
        if key.startswith(sender_prefix):
            if key[len(sender_prefix):] not in senders:
                raise ValueError('Unknown sender state')
            versions[key[len(sender_prefix):]] = int(item['version'])
            continue
        if not key.startswith(result_prefix):
            raise ValueError('Unexpected streaming item')
        result = json.loads(item['payload'])
        event = events.get(result.get('transaction_id'))
        score, features = result.get('model_score'), result.get('features', {})
        if (event is None or key != result_prefix + fingerprint(result['transaction_id'])
                or result.get('input_sha256') != fingerprint(event) or result.get('run_id') != run_id
                or result.get('score_meaning') != SCORE_MEANING or set(features) != set(SENDER_FEATURES)
                or any(type(value) is not int or value < 0 for value in features.values())
                or type(score) not in (int, float) or not 0 <= score <= 1):
            raise ValueError('Committed result does not match the reviewed replay')
        # Result keys are unique per transaction, so a redelivered event cannot add a row.
        rows[result['transaction_id']] = (result, event)
    return rows, versions


def run_state(processed, expected, senders, versions, consumer_enabled, alarm):
    """Returns (state, reconciled). Completion needs every event and every sender version."""
    reconciled = processed == expected and versions == senders
    if alarm == 'ALARM':
        return 'failed', reconciled
    if reconciled:
        return 'completed', True
    if processed == 0:
        return 'waiting', False
    return ('running' if consumer_enabled else 'paused'), False


def display(result, event, scored_at, rank=None, cutoff=None):
    """Allowlisted public fields for one scored transaction; ``review_now`` is None without a cutoff for this model."""
    txn = event['transaction']
    return {'rank': rank, 'transaction_id': txn['transaction_id'], 'timestamp': txn['timestamp'],
            'scored_at': scored_at, 'model_score': result['model_score'],
            'review_now': None if cutoff is None else result['model_score'] >= cutoff, 'amount_usd_asof': txn['amount_usd_asof'],
            'payment_format': txn['payment_format'], 'payment_currency': txn['payment_currency'],
            'receiving_currency': txn['receiving_currency'], 'from_bank': txn['from_bank'],
            'from_account': mask(txn['from_account']), 'to_bank': txn['to_bank'],
            'to_account': mask(txn['to_account']), **result['features']}


def audit_name(run_id, transaction_id):
    """Audit object name the consumer uses for a result (``DynamoStore.audit``)."""
    return fingerprint(run_id + ':result:' + fingerprint(transaction_id))


def progress(times, processed, now):
    """Cumulative committed scores over wall-clock time, evenly thinned to PROGRESS_POINTS.

    A score whose audit copy is not listed yet still counts: the final point
    then uses the publication time so the curve ends at the committed total.
    """
    points = [{'at': iso(moment), 'processed': count} for count, moment in enumerate(sorted(times), 1)]
    if len(points) > PROGRESS_POINTS:
        last = len(points) - 1
        points = [points[round(index * last / (PROGRESS_POINTS - 1))] for index in range(PROGRESS_POINTS)]
    if not points or points[-1]['processed'] != processed:
        points.append({'at': now, 'processed': processed})
    return points


def build_snapshot(items, run_id, events, senders, *, now, consumer_enabled, alarm, committed, model_sha256):
    """``committed`` maps audit object names to their S3 write times (see ``commit_times``)."""
    rows, versions = collect(items, run_id, events, senders)
    scored = {transaction_id: committed.get(audit_name(run_id, transaction_id)) for transaction_id in rows}
    same_model = model_sha256 == REVIEW['model_manifest_sha256']
    cutoff = REVIEW['cutoff'] if same_model else None

    def position(pair):
        """Replay order: event time, then source row (the batch queue's tie policy)."""
        return pair[1]['transaction']['timestamp'], pair[1]['transaction']['source_row_index']

    def public(pair, rank=None):
        moment = scored[pair[0]['transaction_id']]
        return display(*pair, iso(moment) if moment else None, rank, cutoff)
    ranked = sorted(rows.values(), key=lambda pair: (-pair[0]['model_score'], *position(pair)))
    latest = sorted(rows.values(), key=position, reverse=True)[:LATEST]
    state, reconciled = run_state(len(rows), len(events), senders, versions, consumer_enabled, alarm)
    times = [moment for moment in scored.values() if moment]
    points = progress(times, len(rows), now)
    return {'schema_version': SCHEMA_VERSION, 'run_id': run_id, 'generated_at': now, 'state': state,
            'reconciled': reconciled, 'consumer_enabled': consumer_enabled,
            'cohort': 'Replay of complete sender histories from the verified HI-Small batch; synthetic data',
            'expected': len(events), 'processed': len(rows),
            'senders': {'expected': len(senders), 'started': len(versions),
                        'complete': sum(versions.get(key) == final for key, final in senders.items())},
            'replay_position': latest[0][1]['transaction']['timestamp'] if latest else None,
            'max_score': ranked[0][0]['model_score'] if ranked else None,
            'top_k': TOP_K, 'queue': [public(pair, rank) for rank, pair in enumerate(ranked[:TOP_K], 1)],
            'latest': [public(pair) for pair in latest],
            'last_progress_at': iso(max(times)) if times else None, 'progress': points,
            'review': {**REVIEW, 'flagged': sum(result['model_score'] >= cutoff for result, _ in rows.values())}
                      if same_model else None,
            'verification': VERIFIED.get(run_id) if same_model else None,
            'model': {'manifest_sha256': model_sha256, 'score_meaning': SCORE_MEANING}}


def commit_times(s3, bucket, run_id):
    """Write time of each audit copy, keyed by audit name: when that score was committed.

    DynamoDB results carry only historical event time, so this one paginated
    listing is the wall-clock record of the replay. S3 times have one-second precision.
    """
    prefix, times, token = f'streaming/{run_id}/audit/', {}, None
    while True:
        request = {'Bucket': bucket, 'Prefix': prefix}
        if token:
            request['ContinuationToken'] = token
        page = s3.list_objects_v2(**request)
        for entry in page.get('Contents', []):
            times[entry['Key'][len(prefix):].removesuffix('.json')] = entry['LastModified']
        if len(times) > MAX_ITEMS:
            raise ValueError('Audit listing exceeds the reviewed cap')
        if not page.get('IsTruncated'):
            return times
        token = page['NextContinuationToken']


def alarm_state(cloudwatch, name):
    alarms = cloudwatch.describe_alarms(AlarmNames=[name])['MetricAlarms']
    return alarms[0]['StateValue'] if alarms else 'INSUFFICIENT_DATA'


def publish(env, now=None):
    """Build, validate and write one snapshot. Raises before writing if anything is invalid."""
    aws, run_id = clients(), env['RUN_ID']
    key = f'dashboard/streaming/{run_id}/snapshot.json'
    events, senders = reviewed_events(env['INPUT_BUCKET'], env['INPUT_KEY'], env['INPUT_SHA256'], run_id)
    snapshot = build_snapshot(scan_items(aws['dynamodb'], env['STATE_TABLE'], run_id), run_id, events, senders,
                              now=now or iso(datetime.now(timezone.utc)),
                              consumer_enabled=env['CONSUMER_ENABLED'] == 'true',
                              alarm=alarm_state(aws['cloudwatch'], env['ALARM_NAME']),
                              committed=commit_times(aws['s3'], env['INPUT_BUCKET'], run_id),
                              model_sha256=env['MANIFEST_SHA256'])
    body = canonical(snapshot).encode()
    if len(body) > MAX_BYTES:
        raise ValueError('Snapshot exceeds the reviewed size cap')
    # no-store with the distribution's min TTL 0 keeps CloudFront from caching it, as for current.json.
    aws['s3'].put_object(Bucket=env['SITE_BUCKET'], Key=key, Body=body, ContentType='application/json',
                         ServerSideEncryption='AES256', CacheControl='no-store,max-age=0')
    return {'state': snapshot['state'], 'processed': snapshot['processed'], 'expected': snapshot['expected']}


def handler(event, context):
    """Publish now, then every ``PUBLISH_INTERVAL_SECONDS`` within the scheduled minute.

    ``{"once": true}`` publishes a single snapshot, e.g. for a parked or completed run.
    """
    interval = int(os.environ.get('PUBLISH_INTERVAL_SECONDS', '15'))
    deadline = time.monotonic() + WINDOW_SECONDS
    while True:
        summary = publish(os.environ)
        print(canonical({'streaming_snapshot_published': 1, **summary}))
        if (event or {}).get('once') is True or time.monotonic() + interval > deadline:
            return summary
        time.sleep(interval)
