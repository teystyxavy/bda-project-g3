"""Public, read-only pipeline status for the deployed dashboard.

No sign-in and no control actions: reports the latest workflow runs and the
newest run's stages from Step Functions history. Never returns workflow payloads,
ARNs or account identifiers. Responses are cached briefly to bound AWS calls.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

try:
    from bda_pipeline.dashboard_api import describe, history, iso, stage_progress
except ImportError:  # The Lambda package places both modules at its root.
    from dashboard_api import describe, history, iso, stage_progress


RECENT_RUNS = 10
CACHE_SECONDS = 15
RUN_NAME = re.compile('[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
SUBSETS = ('HI-Small', 'HI-Medium', 'HI-Large')
_cache = {'at': 0.0, 'body': None}


@lru_cache(maxsize=1)
def client():
    config = Config(connect_timeout=3, read_timeout=8,
                    retries={'mode': 'standard', 'total_max_attempts': 2},
                    user_agent_appid='IS459-DashboardStatus')
    return boto3.Session().client('stepfunctions', config=config)


def selection(raw):
    """Whitelisted, type-checked run options. Anything else in the input stays private."""
    try:
        event = json.loads(raw or '{}')
    except ValueError:
        event = {}
    if not isinstance(event, dict):
        event = {}
    rows, version, subset = event.get('rows'), event.get('dataset_version'), event.get('subset')
    # Runs started without a subset are HI-Small (the control API omits it for HI-Small).
    return {'subset': subset if subset in SUBSETS else 'HI-Small', 'rows': rows if type(rows) is int else None,
            'dataset_version': version if type(version) is int else None,
            'train': event.get('train') is True}


def latest_detail(states, machine, batch, run, dashboard_url):
    parent = describe(states, machine, run['name'])
    if not parent:
        return None
    child = describe(states, batch, run['name'])
    chosen = selection(parent.get('input'))
    # State-event IDs are local to each execution; process them separately.
    events = history(states, parent['executionArn'])
    stages = stage_progress(parent, child, events + (
        [{**e, 'id': e['id'] + 100000, 'previousEventId': e.get('previousEventId', 0) + 100000}
         for e in history(states, child['executionArn'])] if child else []), chosen['train'])
    stop = parent.get('stopDate')
    result = {'run_id': run['name'], 'status': parent['status'], 'started_at': iso(parent['startDate']),
              'finished_at': iso(stop), 'checked_at': iso(datetime.now(timezone.utc)),
              'elapsed_seconds': max(0, int(((stop or datetime.now(timezone.utc)) - parent['startDate']).total_seconds())),
              'stages': stages, 'selection': chosen,
              'cache_hit': any(e.get('stateEnteredEventDetails', {}).get('name') == 'ExistingRelease' for e in events)}
    if parent['status'] == 'SUCCEEDED':
        try:
            release_id = json.loads(parent.get('output') or '{}').get('run_id', run['name'])
        except (ValueError, AttributeError):
            release_id = None
        # Only reviewed, same-origin result links, never arbitrary workflow URLs.
        if isinstance(release_id, str) and re.fullmatch('[a-z0-9][a-z0-9-]{2,35}', release_id):
            result['release_url'] = dashboard_url + 'releases/' + release_id + '/index.html'
    elif parent['status'] != 'RUNNING':
        result['message'] = 'Run stopped before publication. The last verified release is still shown.'
    return result


def pipeline_status(states, machine, batch, dashboard_url):
    runs = [run for run in states.list_executions(stateMachineArn=machine, maxResults=RECENT_RUNS)['executions']
            if RUN_NAME.fullmatch(run['name'])]
    return {'schema_version': 1, 'read_only': True, 'checked_at': iso(datetime.now(timezone.utc)),
            'latest': latest_detail(states, machine, batch, runs[0], dashboard_url) if runs else None,
            'runs': [{'run_id': run['name'], 'status': run['status'], 'started_at': iso(run['startDate']),
                      'finished_at': iso(run.get('stopDate'))} for run in runs]}


def response(status, body, max_age):
    return {'statusCode': status, 'headers': {'Content-Type': 'application/json',
            'Cache-Control': f'public, max-age={max_age}', 'X-Content-Type-Options': 'nosniff'},
            'body': json.dumps(body, sort_keys=True, separators=(',', ':'))}


def handler(event, context):
    request = event.get('requestContext', {}).get('http', {})
    if request.get('method') != 'GET' or event.get('rawPath', '').rstrip('/') != '/status':
        return response(404, {'message': 'Route not found.'}, 300)
    now = time.monotonic()
    if _cache['body'] is None or now - _cache['at'] >= CACHE_SECONDS:
        try:
            _cache['body'] = pipeline_status(client(), os.environ['ACQUISITION_MACHINE'],
                                             os.environ['BATCH_MACHINE'], os.environ['DASHBOARD_URL'])
            _cache['at'] = now
        except (BotoCoreError, ClientError):
            # Never return provider exception messages.
            return response(503, {'message': 'AWS status is temporarily unavailable.'}, 10)
    return response(200, _cache['body'], 10)
