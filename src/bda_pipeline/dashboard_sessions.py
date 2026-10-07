"""Exchange an IAM-authorized one-time handoff for a bounded dashboard session."""
import hashlib
import json
import re
import secrets
import time

OPERATOR = 'arn:aws:iam::588738581042:user/g1team3'
TOKEN = re.compile(r'[A-Za-z0-9_-]{43}')


class SessionError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def key(kind, token):
    return {'id': kind + ':' + hashlib.sha256(token.encode()).hexdigest()}


def bootstrap(event, table):
    # This context is constructed by API Gateway after SigV4 verification.
    iam = event.get('requestContext', {}).get('authorizer', {}).get('iam', {})
    if iam.get('userArn') != OPERATOR:
        raise SessionError(403, 'Only the configured Group 3 AWS identity can sign in.')
    code = secrets.token_urlsafe(32)
    table.put_item(Item={**key('handoff', code), 'subject': OPERATOR,
                        'expires_at': int(time.time()) + 120}, ConditionExpression='attribute_not_exists(id)')
    return {'code': code, 'expires_in': 120}


def exchange(event, table):
    if len(event.get('body') or '') > 4096:
        raise SessionError(401, 'Sign-in link is invalid or expired.')
    try:
        body = json.loads(event.get('body') or '{}')
        code = body.get('code') if isinstance(body, dict) else None
    except ValueError:
        code = None
    if not isinstance(code, str) or not TOKEN.fullmatch(code):
        raise SessionError(401, 'Sign-in link is invalid or expired. Run the AWS sign-in helper again.')
    try:
        claimed = table.delete_item(Key=key('handoff', code),
            ConditionExpression='expires_at > :now AND subject = :subject',
            ExpressionAttributeValues={':now': int(time.time()), ':subject': OPERATOR}, ReturnValues='ALL_OLD')['Attributes']
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        raise SessionError(401, 'Sign-in link is invalid or expired. Run the AWS sign-in helper again.') from None
    token = secrets.token_urlsafe(32)
    table.put_item(Item={**key('session', token), 'subject': claimed['subject'],
                        'expires_at': int(time.time()) + 3600}, ConditionExpression='attribute_not_exists(id)')
    return {'access_token': token, 'expires_in': 3600, 'token_type': 'Bearer'}


def authenticate(event, table):
    headers = {k.lower(): v for k, v in event.get('headers', {}).items()}
    value = headers.get('authorization', '')
    token = value[7:] if value.startswith('Bearer ') else ''
    if not TOKEN.fullmatch(token):
        raise SessionError(401, 'Sign in through the local AWS helper to manage ingestion.')
    session = table.get_item(Key=key('session', token), ConsistentRead=True).get('Item', {})
    if session.get('subject') != OPERATOR or session.get('expires_at', 0) <= time.time():
        raise SessionError(401, 'Session expired. Run the AWS sign-in helper again.')
    return session['subject'], token


def revoke(table, token):
    table.delete_item(Key=key('session', token))
