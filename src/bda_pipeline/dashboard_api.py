"""Authenticated dashboard control API. Reuses the existing acquisition workflow.

Packaged on its own for Lambda; no data/model libraries or browser credentials.
API Gateway verifies AWS signatures on session bootstrap. This handler restricts
bootstrap to the Group 3 operator, verifies revocable sessions and enforces execution
ownership. Never return workflow payloads.
"""
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


STAGES = [
    ('acquire', 'Acquire dataset and historical FX'),
    ('curate', 'Clean transactions and compute features'),
    ('score', 'Score transactions'),
    ('network', 'Find mule networks'),
    ('baseline', 'Verify rule baseline'),
    ('train', 'Train candidate model'),
    ('export', 'Verify results and build dashboard'),
    ('publish', 'Publish verified results'),
]
STATE_STAGE = {
    'Acquire': 'acquire', 'Begin': 'curate', 'ReuseCurated': 'curate',
    'Curate': 'curate', 'PrepareBaseline': 'curate',
    'ReuseScores': 'score', 'Score': 'score', 'ScoresReused': 'score',
    'ReuseNetwork': 'network', 'Network': 'network', 'NetworkReused': 'network', 'PrepareBranches': 'baseline',
    'TrainRequested': 'train', 'ReuseFrozen': 'train', 'TrainDone': 'train',
    'PrepareExport': 'export', 'Publish': 'publish',
}
for _prefix, _stage in [('Baseline', 'baseline'), ('Train', 'train'), ('Export', 'export')]:
    for _suffix in ['', 'PollInit', 'Wait', 'Status', 'Complete', 'PollIncrement', 'Failed']:
        STATE_STAGE[_prefix + _suffix] = _stage
COMPLETION = {'Acquire': 'acquire', 'PrepareBaseline': 'curate', 'Score': 'score',
              'ScoresReused': 'score', 'Network': 'network', 'NetworkReused': 'network',
              'PrepareBranches': 'baseline', 'TrainDone': 'train', 'Publish': 'publish'}
SKIPS = {'ScoresReused': 'score', 'NetworkReused': 'network', 'ReuseFrozen': 'train'}
FAILURES = {'BaselineFailed': 'baseline', 'TrainFailed': 'train', 'ExportFailed': 'export'}
SERVICE = {
    'Acquire': 'AWS Lambda', 'Begin': 'AWS Lambda', 'Curate': 'AWS Glue',
    'PrepareBaseline': 'AWS Lambda', 'PrepareBranches': 'AWS Lambda',
    'Score': 'AWS Glue', 'Network': 'AWS Glue',
    'PrepareExport': 'AWS Lambda',
    'Publish': 'AWS Lambda (publishing to S3 and CloudFront)',
}
for _state, _stage in STATE_STAGE.items():
    if _state.startswith(('Baseline', 'Train', 'Export')):
        SERVICE.setdefault(_state, 'Amazon SageMaker')
for _state in ['ReuseCurated', 'ReuseScores', 'ScoresReused', 'ReuseNetwork', 'NetworkReused',
               'TrainRequested', 'TrainDone', 'ReuseFrozen']:
    SERVICE[_state] = 'AWS Step Functions'


class ApiError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def owner_prefix(subject):
    return 'ui-' + hashlib.sha256(subject.encode()).hexdigest()[:12] + '-'


def execution_arn(machine, run_id):
    return machine.replace(':stateMachine:', ':execution:') + ':' + run_id


def iso(value):
    return value.astimezone(timezone.utc).isoformat() if value else None


@lru_cache(maxsize=1)
def clients():
    config = Config(connect_timeout=3, read_timeout=8,
                    retries={'mode': 'standard', 'total_max_attempts': 2},
                    user_agent_appid='IS459-Dashboard')
    session = boto3.Session()
    return (session.client('stepfunctions', config=config),
            session.resource('dynamodb', config=config).Table(os.environ['LOCK_TABLE']))


def options():
    return {'schema_version': 1, 'subsets': [
        {'value': 'HI-Small', 'label': 'HI-Small', 'supported': True},
        *[{'value': v, 'label': v + ' (not supported by cloud pipeline)', 'supported': False}
          for v in ['HI-Medium', 'HI-Large', 'LI-Small', 'LI-Medium', 'LI-Large']]],
        'demo_rows': 1000, 'row_bounds': {'minimum': 1, 'maximum': 5100000}, 'poll_seconds': 10,
        'notice': 'New processing can incur AWS charges. Reuse verified inputs by default. '
                  'Training creates a private candidate; it never promotes the model.'}


def validate_submission(body):
    if not isinstance(body, dict) or set(body) - {
            'request_id', 'subset', 'rows', 'dataset_version', 'train', 'refresh', 'rerun'}:
        raise ApiError(400, 'Unsupported run options.')
    request_id = body.get('request_id', '')
    if not isinstance(request_id, str) or not re.fullmatch('[0-9a-f]{32}', request_id):
        raise ApiError(400, 'A valid submission ID is required.')
    if body.get('subset') != 'HI-Small':
        raise ApiError(400, 'The cloud pipeline currently supports HI-Small only.')
    rows = body.get('rows')
    if rows is not None and (type(rows) is not int or not 1 <= rows <= 5100000):
        raise ApiError(400, 'Row limit must be an integer from 1 to 5,100,000, or choose the full dataset.')
    version = body.get('dataset_version')
    if version is not None and (type(version) is not int or not 1 <= version <= 100000):
        raise ApiError(400, 'Dataset version must be a positive integer.')
    flags = {}
    for flag in ['train', 'refresh', 'rerun']:
        if type(body.get(flag, False)) is not bool:
            raise ApiError(400, 'Run switches must be true or false.')
        flags[flag] = body.get(flag, False)
    if flags['train'] and rows is not None:
        raise ApiError(400, 'Candidate training requires the full dataset.')
    return request_id, {'rows': rows, 'dataset_version': version, **flags}


def describe(states, machine, run_id):
    try:
        return states.describe_execution(executionArn=execution_arn(machine, run_id))
    except states.exceptions.ExecutionDoesNotExist:
        return None


def reserve(states, table, machine, batch, run_id, digest):
    """Serialize UI launches, including the window before StartExecution is visible.

    The existing batch lock remains authoritative for CLI and worker safety.
    A reservation is only reclaimed after confirmed workflow termination, or
    after five minutes when no execution exists (failed submission).
    """
    current = table.get_item(Key={'id': 'launch'}, ConsistentRead=True).get('Item')
    if current and current['run_id'] == run_id:
        if current['digest'] != digest:
            raise ApiError(409, 'This submission ID was used with different options.')
        return
    if current:
        previous = describe(states, machine, current['run_id'])
        if (previous and previous['status'] == 'RUNNING') or (
                previous is None and time.time() < int(current['reserved_at']) + 300):
            raise ApiError(409, 'Another ingestion is active or being submitted. Check recent runs.')
    # Also refuse starts while an existing command-driven workflow is running.
    for workflow in [machine, batch]:
        if states.list_executions(stateMachineArn=workflow, statusFilter='RUNNING', maxResults=1)['executions']:
            raise ApiError(409, 'The pipeline already has an active run. Wait for it to finish.')
    condition = Attr('run_id').eq(current['run_id']) if current else Attr('id').not_exists()
    try:
        table.put_item(Item={'id': 'launch', 'run_id': run_id, 'digest': digest,
                             'reserved_at': int(time.time())}, ConditionExpression=condition)
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        raise ApiError(409, 'Another ingestion was just submitted. Check recent runs.') from None


def submit(states, table, machine, batch, subject, body):
    request_id, event = validate_submission(body)
    run_id = owner_prefix(subject) + hashlib.sha256(request_id.encode()).hexdigest()[:12]
    event['run_id'] = run_id
    payload = canonical(event)
    existing = describe(states, machine, run_id)
    if existing:
        if canonical(json.loads(existing['input'])) != payload:
            raise ApiError(409, 'This submission ID was used with different options.')
        return {'run_id': run_id, 'status': existing['status'], 'resumed_submission': True}
    reserve(states, table, machine, batch, run_id, hashlib.sha256(payload.encode()).hexdigest())
    # Keep the reservation on transport errors: the workflow might have started.
    # Retrying this request uses the same name/input, never a new execution.
    try:
        states.start_execution(stateMachineArn=machine, name=run_id, input=payload)
    except states.exceptions.ExecutionAlreadyExists:
        existing = describe(states, machine, run_id)
        if not existing or canonical(json.loads(existing['input'])) != payload:
            raise ApiError(409, 'Submission conflict. Check recent runs.') from None
    return {'run_id': run_id, 'status': 'RUNNING'}


def stage_progress(parent, child, events, train):
    stages = {key: {'id': key, 'label': label, 'status': 'pending', 'started_at': None,
                    'active_service': None}
              for key, label in STAGES}
    if not train:
        stages['train']['status'] = 'skipped'
    active = {}
    for event in events:
        entered = event.get('stateEnteredEventDetails')
        exited = event.get('stateExitedEventDetails')
        if entered and entered['name'] in STATE_STAGE:
            name = entered['name']
            stage = STATE_STAGE[name]
            active[event['id']] = (stage, name)
            item = stages[stage]
            item['active_service'] = SERVICE.get(name, 'AWS Step Functions')
            item['started_at'] = item['started_at'] or iso(event['timestamp'])
            if item['status'] not in ['complete', 'reused', 'skipped']:
                item['status'] = 'running'
            if name in SKIPS:
                item['status'] = 'reused' if stage != 'train' else 'skipped'
            if name in FAILURES:
                item['status'] = 'failed'
            if name == 'Publish':
                stages['export']['status'] = 'complete'
        if exited:
            name = exited['name']
            if name in COMPLETION:
                stage = COMPLETION[name]
                if stages[stage]['status'] != 'reused':
                    stages[stage]['status'] = 'complete'
                # Reaching PrepareBaseline without Curate means curated reuse.
                if name == 'PrepareBaseline' and not any(
                        e.get('stateEnteredEventDetails', {}).get('name') == 'Curate' for e in events):
                    stages['curate']['status'] = 'reused'
            if name == 'ScoreAndAnalyze':
                for stage in ['score', 'network', 'baseline']:
                    if stages[stage]['status'] == 'running':
                        stages[stage]['status'] = 'complete'
            if name == 'PrepareExport':
                stages['export']['status'] = 'running'
            if name == 'Publish':
                stages['export']['status'] = 'complete'
        # Task failures link to the state-enter event through previousEventId.
        previous = active.get(event.get('previousEventId'))
        if previous:
            active[event['id']] = previous
            if event['type'].endswith('Failed') or event['type'].endswith('TimedOut'):
                stages[previous[0]]['status'] = 'failed'
    cached = any(e.get('stateEnteredEventDetails', {}).get('name') == 'ExistingRelease' for e in events)
    if parent['status'] == 'SUCCEEDED' and cached:
        for key in stages:
            if key != 'acquire':
                stages[key]['status'] = 'reused' if key != 'train' else 'skipped'
        stages['acquire']['status'] = 'complete'
    if parent['status'] == 'SUCCEEDED' and child:
        for item in stages.values():
            if item['status'] in ['running', 'pending']:
                item['status'] = 'complete'
    if parent['status'] not in ['RUNNING', 'SUCCEEDED']:
        for item in stages.values():
            if item['status'] == 'running':
                item['status'] = 'stopped'
    for item in stages.values():
        if item['status'] != 'running':
            item['active_service'] = None
    return list(stages.values())


def history(states, arn):
    # Payloads may contain account identifiers; never request them for progress.
    return [event for page in states.get_paginator('get_execution_history').paginate(
        executionArn=arn, includeExecutionData=False, PaginationConfig={'PageSize': 1000})
        for event in page['events']]


def run_status(states, machine, batch, subject, run_id, dashboard_url):
    if not isinstance(run_id, str) or not re.fullmatch(re.escape(owner_prefix(subject)) + '[0-9a-f]{12}', run_id):
        raise ApiError(404, 'Run not found.')
    parent = describe(states, machine, run_id)
    if not parent:
        raise ApiError(404, 'Run not found. A new submission may take a few seconds to appear.')
    child = describe(states, batch, run_id)
    event = json.loads(parent['input'])
    # State-event IDs are local to each execution; process them separately.
    events = history(states, parent['executionArn'])
    stages = stage_progress(parent, child, events + (
        [{**e, 'id': e['id'] + 100000, 'previousEventId': e.get('previousEventId', 0) + 100000}
         for e in history(states, child['executionArn'])] if child else []), event.get('train', False))
    stop = parent.get('stopDate')
    result = {'run_id': run_id, 'status': parent['status'], 'started_at': iso(parent['startDate']),
              'finished_at': iso(stop), 'checked_at': iso(datetime.now(timezone.utc)),
              'elapsed_seconds': max(0, int(((stop or datetime.now(timezone.utc)) - parent['startDate']).total_seconds())),
              'stages': stages, 'selection': {'subset': 'HI-Small', **{k: event.get(k) for k in
                  ['rows', 'dataset_version', 'train', 'refresh', 'rerun']}},
              'cache_hit': False}
    if parent['status'] == 'SUCCEEDED':
        output = json.loads(parent.get('output', '{}'))
        # Only return reviewed, same-origin result links, never arbitrary workflow URLs.
        release_id = output.get('run_id', run_id)
        if re.fullmatch('[a-z0-9][a-z0-9-]{2,35}', release_id):
            result['release_url'] = dashboard_url + 'releases/' + release_id + '/index.html'
        result['dashboard_url'] = dashboard_url
        result['cache_hit'] = any(e.get('stateEnteredEventDetails', {}).get('name') == 'ExistingRelease' for e in events)
    elif parent['status'] != 'RUNNING':
        result['message'] = 'Run stopped before publication. Check private AWS evidence before retrying.'
    return result


def list_runs(states, machine, subject):
    runs = []
    # Bounded recent history. Never expose other operators' run names or payloads.
    for page in states.get_paginator('list_executions').paginate(
            stateMachineArn=machine, PaginationConfig={'PageSize': 100, 'MaxItems': 500}):
        for run in page['executions']:
            if run['name'].startswith(owner_prefix(subject)):
                runs.append({'run_id': run['name'], 'status': run['status'],
                             'started_at': iso(run['startDate']), 'finished_at': iso(run.get('stopDate'))})
                if len(runs) == 20:
                    return {'runs': runs}
    return {'runs': runs}


def response(status, body):
    return {'statusCode': status, 'headers': {'Content-Type': 'application/json',
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}, 'body': canonical(body)}


def handler(event, context):
    try:
        from bda_pipeline.dashboard_sessions import authenticate, bootstrap, exchange, revoke, SessionError
    except ImportError:
        from dashboard_sessions import authenticate, bootstrap, exchange, revoke, SessionError
    try:
        method = event['requestContext']['http']['method']
        path = event.get('rawPath', '').rstrip('/')
        states, table = clients()
        if method == 'POST' and path == '/session/bootstrap':
            return response(200, bootstrap(event, table))
        if method == 'POST' and path == '/session/exchange':
            return response(200, exchange(event, table))
        subject, token = authenticate(event, table)
        if method == 'POST' and path == '/session/logout':
            revoke(table, token)
            return response(200, {'status': 'signed-out'})
        if method == 'GET' and path == '/options':
            return response(200, options())
        machine, batch = os.environ['ACQUISITION_MACHINE'], os.environ['BATCH_MACHINE']
        if method == 'GET' and path == '/runs':
            return response(200, list_runs(states, machine, subject))
        if method == 'GET' and path.startswith('/runs/'):
            return response(200, run_status(states, machine, batch, subject, path[6:], os.environ['DASHBOARD_URL']))
        if method == 'POST' and path == '/runs':
            raw = event.get('body') or ''
            if len(raw) > 4096:
                raise ApiError(400, 'Submission is too large.')
            try:
                if event.get('isBase64Encoded'):
                    raw = base64.b64decode(raw, validate=True).decode('utf-8')
                body = json.loads(raw)
            except (ValueError, UnicodeError):
                raise ApiError(400, 'Invalid JSON submission.') from None
            return response(202, submit(states, table, machine, batch, subject, body))
        raise ApiError(404, 'Route not found.')
    except (ApiError, SessionError) as error:
        return response(error.status, {'message': error.message})
    except (BotoCoreError, ClientError):
        # Never log tokens, workflow input/output or provider exception messages.
        return response(503, {'message': 'AWS status is temporarily unavailable. Retry the same submission or refresh status.'})
