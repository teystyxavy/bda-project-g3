"""The local helper checks AWS identity before requesting a browser handoff."""
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from botocore.credentials import Credentials

spec = importlib.util.spec_from_file_location('dashboard_signin',
    Path(__file__).resolve().parents[1] / 'scripts/aws/dashboard-signin.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def setup(monkeypatch, arn, api='https://fixture.execute-api.ap-southeast-1.amazonaws.com'):
    requests = []
    session = SimpleNamespace(client=lambda *args, **kwargs: SimpleNamespace(
        get_caller_identity=lambda: {'Arn': arn}),
        get_credentials=lambda: Credentials('fixture-key', 'fixture-secret', 'fixture-token'))
    monkeypatch.setattr(helper.boto3, 'Session', lambda **kwargs: session)

    def open_request(request, **kwargs):
        requests.append(request)
        payload = {'auth_mode': 'aws-iam-helper', 'api_url': api} if isinstance(request, str) else {'code': 'a' * 43}
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(helper.urllib.request, 'urlopen', open_request)
    return requests


def test_other_aws_identity_never_requests_handoff(monkeypatch):
    requests = setup(monkeypatch, helper.OPERATOR + '-other')
    with pytest.raises(ValueError, match='g1team3'):
        helper.login_url('fixture')
    assert requests == []


def test_handoff_is_aws_signed_and_kept_out_of_query_string(monkeypatch):
    requests = setup(monkeypatch, helper.OPERATOR)
    url = helper.login_url('fixture')
    assert url == helper.ROOT + '#operator_code=' + 'a' * 43
    assert '?' not in url
    request = requests[1]
    assert request.get_method() == 'POST' and request.data == b'{}'
    assert 'AWS4-HMAC-SHA256' in request.get_header('Authorization')
    assert '/ap-southeast-1/execute-api/aws4_request' in request.get_header('Authorization')


def test_unexpected_endpoint_is_rejected_before_signing(monkeypatch):
    requests = setup(monkeypatch, helper.OPERATOR, 'https://untrusted.example')
    with pytest.raises(ValueError, match='configuration'):
        helper.login_url('fixture')
    assert len(requests) == 1
