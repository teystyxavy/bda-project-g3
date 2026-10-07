"""IAM handoffs are identity-bound, single-use, expiring and revocable."""
import json
from types import SimpleNamespace

import pytest
from bda_pipeline import dashboard_sessions as sessions


class Conditional(Exception):
    pass


class Table:
    def __init__(self):
        self.items = {}
        self.meta = SimpleNamespace(client=SimpleNamespace(exceptions=SimpleNamespace(ConditionalCheckFailedException=Conditional)))

    def put_item(self, Item, **kwargs):
        if Item['id'] in self.items:
            raise Conditional()
        self.items[Item['id']] = Item

    def get_item(self, Key, **kwargs):
        return {'Item': self.items.get(Key['id'], {})}

    def delete_item(self, Key, **kwargs):
        row = self.items.get(Key['id'])
        if kwargs.get('ConditionExpression') and (not row or row['expires_at'] <= kwargs['ExpressionAttributeValues'][':now'] or row['subject'] != sessions.OPERATOR):
            raise Conditional()
        return {'Attributes': self.items.pop(Key['id'], {})}


def handoff(table, arn=sessions.OPERATOR):
    return sessions.bootstrap({'requestContext': {'authorizer': {'iam': {'userArn': arn}}}}, table)


def test_only_exact_iam_identity_can_bootstrap():
    for arn in ['', sessions.OPERATOR+'-other', 'arn:aws:iam::588738581042:root']:
        table = Table()
        with pytest.raises(sessions.SessionError) as error:
            handoff(table, arn)
        assert error.value.status == 403 and not table.items


def test_single_use_handoff_session_expiry_and_logout(monkeypatch):
    monkeypatch.setattr(sessions.time, 'time', lambda: 1000)
    table = Table()
    code = handoff(table)['code']
    assert code not in json.dumps(table.items)
    request = {'body': json.dumps({'code': code})}
    token = sessions.exchange(request, table)['access_token']
    assert token not in json.dumps(table.items)
    event = {'headers': {'Authorization': 'Bearer '+token}}
    assert sessions.authenticate(event, table)[0] == sessions.OPERATOR
    with pytest.raises(sessions.SessionError):
        sessions.exchange(request, table)
    monkeypatch.setattr(sessions.time, 'time', lambda: 4600)
    with pytest.raises(sessions.SessionError):
        sessions.authenticate(event, table)
    monkeypatch.setattr(sessions.time, 'time', lambda: 1001)
    sessions.revoke(table, token)
    with pytest.raises(sessions.SessionError):
        sessions.authenticate(event, table)


def test_expired_and_malformed_codes_are_rejected(monkeypatch):
    monkeypatch.setattr(sessions.time, 'time', lambda: 1000)
    table = Table()
    code = handoff(table)['code']
    monkeypatch.setattr(sessions.time, 'time', lambda: 1120)
    for raw in ['not json', '[]', '{}', json.dumps({'code': code})]:
        with pytest.raises(sessions.SessionError):
            sessions.exchange({'body': raw}, table)
