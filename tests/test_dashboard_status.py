"""Public status endpoint tests: no AWS calls, no paid jobs, no real credentials."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError

from bda_pipeline.dashboards import dashboard_api as api
from bda_pipeline.dashboards import dashboard_status as status

MACHINE = 'arn:aws:states:ap-southeast-1:588738581042:stateMachine:is459-g3-cloud-ingest'
BATCH = MACHINE.replace('cloud-ingest', 'ingest')
NOW = datetime.now(timezone.utc)


class FakeStates:
    def __init__(self):
        self.exceptions = boto3.Session(aws_access_key_id='test', aws_secret_access_key='test',
                                        region_name='ap-southeast-1').client('stepfunctions').exceptions
        self.executions, self.events, self.calls, self.fail = {}, {}, [], False

    def add(self, machine, name, run_status, started, **extra):
        arn = api.execution_arn(machine, name)
        self.executions[arn] = {'executionArn': arn, 'name': name, 'status': run_status,
                                'startDate': started, **extra}
        return arn

    def list_executions(self, **request):
        self.calls.append(('list', request))
        if self.fail:
            raise EndpointConnectionError(endpoint_url='https://example.invalid')
        prefix = request['stateMachineArn'].replace(':stateMachine:', ':execution:') + ':'
        runs = sorted((run for arn, run in self.executions.items() if arn.startswith(prefix)),
                      key=lambda run: run['startDate'], reverse=True)
        return {'executions': [{key: run[key] for key in ['name', 'status', 'startDate']
                                if key in run} for run in runs][:request['maxResults']]}

    def describe_execution(self, executionArn):
        self.calls.append(('describe', executionArn))
        if executionArn not in self.executions:
            raise self.exceptions.ExecutionDoesNotExist({'Error': {'Code': 'ExecutionDoesNotExist'}}, 'DescribeExecution')
        return self.executions[executionArn]

    def get_paginator(self, name):
        def paginate(**request):
            self.calls.append(('history', request))
            assert request['includeExecutionData'] is False
            yield {'events': self.events.get(request['executionArn'], [])}
        return SimpleNamespace(paginate=paginate)


@pytest.fixture
def states(monkeypatch):
    fake = FakeStates()
    monkeypatch.setattr(status, 'client', lambda: fake)
    monkeypatch.setattr(status, '_cache', {'at': 0.0, 'body': None})
    for key, value in [('ACQUISITION_MACHINE', MACHINE), ('BATCH_MACHINE', BATCH),
                       ('DASHBOARD_URL', 'https://example.cloudfront.net/')]:
        monkeypatch.setenv(key, value)
    return fake


def get(path='/status', method='GET'):
    return {'rawPath': path, 'requestContext': {'http': {'method': method}}}


def entries(names, offset=0):
    return [{'id': i + 1, 'type': 'TaskStateEntered', 'timestamp': NOW + timedelta(seconds=offset + i),
             'stateEnteredEventDetails': {'name': name}} for i, name in enumerate(names)]


def test_running_run_reports_stages_without_payloads_or_arns(states):
    secret = {'run_id': 'live-run', 'rows': 1000, 'train': False, 'bucket': 'private-bucket-588738581042'}
    parent = states.add(MACHINE, 'live-run', 'RUNNING', NOW, input=json.dumps(secret))
    child = states.add(BATCH, 'live-run', 'RUNNING', NOW, input='{}')
    states.add(MACHINE, 'older-run', 'SUCCEEDED', NOW - timedelta(hours=1), stopDate=NOW - timedelta(minutes=30))
    states.events[parent] = entries(['Acquire'])
    states.events[child] = entries(['Begin', 'Curate'], offset=5)
    response = status.handler(get(), None)
    assert response['statusCode'] == 200
    assert response['headers']['Cache-Control'] == 'public, max-age=10'
    data = json.loads(response['body'])
    assert data['read_only'] is True
    assert [run['run_id'] for run in data['runs']] == ['live-run', 'older-run']
    stages = {stage['id']: stage for stage in data['latest']['stages']}
    assert stages['curate']['status'] == 'running' and stages['curate']['active_service'] == 'AWS Glue'
    assert stages['train']['status'] == 'skipped'
    assert data['latest']['selection'] == {'subset': 'HI-Small', 'rows': 1000, 'dataset_version': None, 'train': False}
    assert 'arn:aws' not in response['body'] and '588738581042' not in response['body']
    assert 'private-bucket' not in response['body']


def test_succeeded_run_links_only_same_origin_release(states):
    states.add(MACHINE, 'full-run', 'SUCCEEDED', NOW, stopDate=NOW + timedelta(minutes=20),
               input='{"train": true}', output=json.dumps({'run_id': 'full-run'}))
    data = json.loads(status.handler(get(), None)['body'])
    assert data['latest']['release_url'] == 'https://example.cloudfront.net/releases/full-run/index.html'
    assert data['latest']['elapsed_seconds'] == 1200
    states.executions[api.execution_arn(MACHINE, 'full-run')]['output'] = json.dumps({'run_id': 'https://evil.example/'})
    status._cache['body'] = None
    assert 'release_url' not in json.loads(status.handler(get(), None)['body'])['latest']


def test_failed_run_explains_last_release_is_kept(states):
    states.add(MACHINE, 'bad-run', 'FAILED', NOW, stopDate=NOW, input='not json')
    latest = json.loads(status.handler(get(), None)['body'])['latest']
    assert latest['status'] == 'FAILED' and 'last verified release' in latest['message']
    assert latest['selection']['train'] is False


def test_no_runs_yet(states):
    data = json.loads(status.handler(get(), None)['body'])
    assert data['latest'] is None and data['runs'] == []


def test_responses_are_cached_to_bound_aws_calls(states):
    states.add(MACHINE, 'run', 'SUCCEEDED', NOW, stopDate=NOW)
    status.handler(get(), None)
    calls = len(states.calls)
    for _ in range(5):
        assert status.handler(get(), None)['statusCode'] == 200
    assert len(states.calls) == calls


@pytest.mark.parametrize('path,method', [('/runs', 'GET'), ('/status', 'POST'), ('/status/../runs', 'GET')])
def test_only_read_only_status_route(states, path, method):
    assert status.handler(get(path, method), None)['statusCode'] == 404
    assert not states.calls


def test_aws_errors_are_generic(states):
    states.fail = True
    response = status.handler(get(), None)
    assert response['statusCode'] == 503
    assert 'example.invalid' not in response['body']


def test_unexpected_run_names_are_not_echoed(states):
    states.add(MACHINE, '<script>', 'SUCCEEDED', NOW, stopDate=NOW)
    data = json.loads(status.handler(get(), None)['body'])
    assert data['runs'] == [] and data['latest'] is None
