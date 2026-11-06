"""Control-plane tests: no AWS calls, no paid jobs, no real credentials."""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError

from bda_pipeline import dashboard_api as api

MACHINE = 'arn:aws:states:ap-southeast-1:588738581042:stateMachine:is459-g3-cloud-ingest'
BATCH = MACHINE.replace('cloud-ingest', 'ingest')
SUBJECT = 'arn:aws:iam::588738581042:user/g1team3'
NOW = datetime.now(timezone.utc)


@pytest.fixture
def states():
    session = boto3.Session(aws_access_key_id='test', aws_secret_access_key='test', region_name='ap-southeast-1')
    exceptions = session.client('stepfunctions').exceptions
    db_exceptions = session.client('dynamodb').exceptions

    class FakeStates:
        def __init__(self):
            self.exceptions = exceptions
            self.executions = {}
            self.events = {}
            self.starts = []
            self.transport_error = False
            self.calls = []

        def describe_execution(self, executionArn):
            self.calls.append(('describe', executionArn))
            if executionArn not in self.executions:
                raise self.exceptions.ExecutionDoesNotExist({'Error': {'Code': 'ExecutionDoesNotExist'}}, 'DescribeExecution')
            return self.executions[executionArn]

        def start_execution(self, **request):
            arn = api.execution_arn(request['stateMachineArn'], request['name'])
            if arn not in self.executions:
                self.starts.append(request)
                self.executions[arn] = {'executionArn': arn, 'status': 'RUNNING',
                    'startDate': NOW, 'input': request['input']}
            if self.transport_error:
                raise EndpointConnectionError(endpoint_url='https://example.invalid')
            return {'executionArn': arn, 'startDate': NOW}

        def list_executions(self, **request):
            prefix = request['stateMachineArn'].replace(':stateMachine:', ':execution:') + ':'
            items = [{'name': arn.split(':')[-1], **run} for arn, run in self.executions.items()
                     if arn.startswith(prefix) and (not request.get('statusFilter') or run['status'] == request['statusFilter'])]
            return {'executions': items}

        def get_paginator(self, name):
            def paginate(**request):
                if name == 'list_executions':
                    yield self.list_executions(**request)
                else:
                    assert request['includeExecutionData'] is False
                    yield {'events': self.events.get(request['executionArn'], [])}
            return SimpleNamespace(paginate=paginate)

    class FakeTable:
        def __init__(self):
            self.item = None
            self.fail_condition = False
            self.meta = SimpleNamespace(client=SimpleNamespace(exceptions=db_exceptions))

        def get_item(self, **request):
            assert request['ConsistentRead'] is True
            if request['Key']['id'].startswith('session:'):
                return {'Item': {'subject': SUBJECT, 'expires_at': 9999999999}}
            return {'Item': copy.deepcopy(self.item)} if self.item else {}

        def put_item(self, **request):
            condition = request['ConditionExpression'].get_expression()
            valid = self.item is None if condition['operator'] == 'attribute_not_exists' else (
                self.item and self.item['run_id'] == condition['values'][1])
            if self.fail_condition or not valid:
                raise self.meta.client.exceptions.ConditionalCheckFailedException(
                    {'Error': {'Code': 'ConditionalCheckFailedException'}}, 'PutItem')
            self.item = request['Item']

    return FakeStates(), FakeTable()


def body(**extra):
    return {'request_id': 'a' * 32, 'subset': 'HI-Small', 'rows': 1000, **extra}


def event(method='POST', path='/runs', payload=None, claims=None):
    return {'headers': {'authorization': 'Bearer ' + 'a'*43}, 'rawPath': path, 'body': json.dumps(body() if payload is None else payload),
            'requestContext': {'http': {'method': method}, 'authorizer': {'jwt': {'claims': claims or {
                'sub': SUBJECT, 'token_use': 'access', 'cognito:groups': '[ingestion-operators]'}}}}}


@pytest.fixture
def control(states, monkeypatch):
    monkeypatch.setattr(api, 'clients', lambda: states)
    for key, value in [('ACQUISITION_MACHINE', MACHINE), ('BATCH_MACHINE', BATCH),
                       ('DASHBOARD_URL', 'https://example.cloudfront.net/')]:
        monkeypatch.setenv(key, value)
    return states


def test_retry_after_lost_start_response_never_starts_twice(control):
    states, table = control
    states.transport_error = True
    assert api.handler(event(), None)['statusCode'] == 503
    assert table.item and len(states.starts) == 1
    states.transport_error = False
    response = api.handler(event(), None)
    assert response['statusCode'] == 202
    assert json.loads(response['body'])['resumed_submission'] is True
    assert len(states.starts) == 1


def test_same_submission_id_cannot_change_options(control):
    assert api.handler(event(), None)['statusCode'] == 202
    assert api.handler(event(payload=body(refresh=True)), None)['statusCode'] == 409
    assert len(control[0].starts) == 1


def test_active_run_blocks_other_user_and_request(control):
    assert api.handler(event(), None)['statusCode'] == 202
    assert api.handler(event(payload=body(request_id='b' * 32)), None)['statusCode'] == 409
    assert len(control[0].starts) == 1


def test_atomic_reservation_refuses_concurrent_submission(control):
    control[1].fail_condition = True
    assert api.handler(event(), None)['statusCode'] == 409
    assert not control[0].starts


def test_existing_cli_run_blocks_ui_launch(control):
    states, _ = control
    arn = api.execution_arn(BATCH, 'cli-run')
    states.executions[arn] = {'status': 'RUNNING', 'startDate': NOW}
    assert api.handler(event(), None)['statusCode'] == 409
    assert not states.starts


def test_terminal_reservation_can_be_replaced(control):
    states, _ = control
    assert api.handler(event(), None)['statusCode'] == 202
    next(iter(states.executions.values()))['status'] = 'SUCCEEDED'
    assert api.handler(event(payload=body(request_id='b' * 32)), None)['statusCode'] == 202
    assert len(states.starts) == 2


@pytest.mark.parametrize('changes', [
    {'subset': 'LI-Small'}, {'rows': True}, {'rows': 5_100_001}, {'rows': 0}, {'rows': 1.5}, {'train': True},
    {'dataset_version': False}, {'dataset_version': -1}, {'refresh': 'true'},
    {'request_id': '../unsafe'}, {'stateMachineArn': 'anything'},
])
def test_reject_unreviewed_or_malformed_inputs_before_aws(control, changes):
    assert api.handler(event(payload=body(**changes)), None)['statusCode'] == 400
    assert not control[0].starts and control[1].item is None


@pytest.mark.parametrize('authorization', ['', 'Bearer malformed', 'Basic invalid'])
def test_session_required(control, authorization):
    request = event()
    request['headers'] = {'authorization': authorization}
    assert api.handler(request, None)['statusCode'] == 401
    assert not control[0].calls


@pytest.mark.parametrize('rows', [1, 2500, 5100000])
def test_custom_rows_match_script_bounds(control, rows):
    assert api.handler(event(payload=body(rows=rows)), None)['statusCode'] == 202
    assert json.loads(control[0].starts[0]['input'])['rows'] == rows


def test_cannot_inspect_other_operators_execution(control):
    run_id = api.owner_prefix('someone-else') + 'b' * 12
    assert api.handler(event('GET', '/runs/' + run_id), None)['statusCode'] == 404
    assert not control[0].calls


def entries(names):
    return [{'id': i+1, 'type': 'TaskStateEntered', 'timestamp': NOW + timedelta(seconds=i),
             'stateEnteredEventDetails': {'name': name}} for i, name in enumerate(names)]


def statuses(stages):
    return {item['id']: item['status'] for item in stages}


def test_parallel_progress_and_cache_reuse_are_reported():
    events = entries(['Acquire', 'Begin', 'ReuseCurated', 'PrepareBaseline', 'ScoresReused', 'Network', 'BaselineWait'])
    events.insert(4, {'id': 100, 'type': 'TaskStateExited', 'timestamp': NOW,
                      'stateExitedEventDetails': {'name': 'PrepareBaseline'}})
    result = statuses(api.stage_progress({'status': 'RUNNING'}, {'status': 'RUNNING'}, events, False))
    assert result['curate'] == 'reused'
    assert result['score'] == 'reused'
    assert result['baseline'] == result['network'] == 'running'
    assert result['train'] == 'skipped' and result['export'] == 'pending'


def test_failed_worker_is_failed_and_other_active_stages_stopped():
    events = entries(['Curate', 'Score', 'Network'])
    events.extend([{'id': 4, 'previousEventId': 2, 'type': 'TaskScheduled', 'timestamp': NOW},
                   {'id': 5, 'previousEventId': 4, 'type': 'TaskFailed', 'timestamp': NOW}])
    result = statuses(api.stage_progress({'status': 'FAILED'}, {'status': 'FAILED'}, events, False))
    assert result['score'] == 'failed'
    assert result['curate'] == result['network'] == 'stopped'
    assert result['publish'] == 'pending'


def test_successful_cached_release_labels_processing_as_reused():
    result = statuses(api.stage_progress({'status': 'SUCCEEDED'}, None,
                                         entries(['Acquire', 'ExistingRelease']), False))
    assert result['acquire'] == 'complete' and result['curate'] == 'reused'
    assert result['train'] == 'skipped'


def test_status_never_exposes_payloads_errors_or_arbitrary_links(control):
    states, _ = control
    receipt = json.loads(api.handler(event(), None)['body'])
    run_id = receipt['run_id']
    arn = api.execution_arn(MACHINE, run_id)
    states.executions[arn].update(status='SUCCEEDED', stopDate=NOW + timedelta(seconds=30),
        output=json.dumps({'run_id': 'older-run', 'release_url': 'https://evil.invalid/', 'secret': 'PRIVATE'}))
    states.events[arn] = entries(['Acquire', 'ExistingRelease'])
    response = api.handler(event('GET', '/runs/' + run_id), None)
    result = json.loads(response['body'])
    assert response['statusCode'] == 200 and result['cache_hit'] is True
    assert result['release_url'] == 'https://example.cloudfront.net/releases/older-run/index.html'
    assert result['elapsed_seconds'] == 30
    assert 'PRIVATE' not in response['body'] and 'evil.invalid' not in response['body']


def test_recent_history_is_scoped_to_signed_in_operator(control):
    states, _ = control
    api.handler(event(), None)
    states.executions[api.execution_arn(MACHINE, api.owner_prefix('another-user') + 'c'*12)] = {
        'status': 'RUNNING', 'startDate': NOW}
    result = json.loads(api.handler(event('GET', '/runs'), None)['body'])
    assert len(result['runs']) == 1
    assert result['runs'][0]['run_id'].startswith(api.owner_prefix(SUBJECT))


def test_invalid_base64_is_a_validation_error(control):
    request = {**event(), 'body': 'not base64!', 'isBase64Encoded': True}
    assert api.handler(request, None)['statusCode'] == 400
    assert not control[0].starts


def test_active_service_distinguishes_control_step_from_glue_job():
    parent, child = {'status': 'RUNNING'}, {'status': 'RUNNING'}
    starting = api.stage_progress(parent, child, entries(['Begin']), False)
    running = api.stage_progress(parent, child, entries(['Begin', 'Curate']), False)
    assert next(item for item in starting if item['id'] == 'curate')['active_service'] == 'AWS Lambda'
    assert next(item for item in running if item['id'] == 'curate')['active_service'] == 'AWS Glue'


def test_parallel_branches_name_their_active_aws_services():
    stages = api.stage_progress({'status': 'RUNNING'}, {'status': 'RUNNING'},
                                entries(['Score', 'Network', 'BaselineWait']), False)
    active = {item['id']: item['active_service'] for item in stages if item['status'] == 'running'}
    assert active == {'score': 'AWS Glue', 'network': 'AWS Glue', 'baseline': 'AWS Glue'}
