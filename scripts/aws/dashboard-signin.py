"""Open the live dashboard using the existing Group 3 AWS identity; no browser AWS credentials."""
import argparse
import json
import urllib.request
import webbrowser

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

ROOT = 'https://d1n61tlw9pcrog.cloudfront.net/'
OPERATOR = 'arn:aws:iam::588738581042:user/g1team3'


def login_url(profile):
    session = boto3.Session(profile_name=profile, region_name='ap-southeast-1')
    sts = session.client('sts', config=Config(connect_timeout=5, read_timeout=15,
        retries={'mode': 'standard', 'total_max_attempts': 2}))
    if sts.get_caller_identity()['Arn'] != OPERATOR:
        raise ValueError('Sign in with the configured Group 3 g1team3 AWS identity.')
    with urllib.request.urlopen(ROOT + 'ingestion-config.json', timeout=20) as response:
        config = json.load(response)
    import re
    if config.get('auth_mode') != 'aws-iam-helper' or not re.fullmatch(
            r'https://[a-z0-9]+\.execute-api\.ap-southeast-1\.amazonaws\.com', config.get('api_url', '')):
        raise ValueError('Unexpected dashboard control configuration')
    url = config['api_url'] + '/session/bootstrap'
    request = AWSRequest(method='POST', url=url, data=b'{}', headers={'Content-Type': 'application/json'})
    SigV4Auth(session.get_credentials().get_frozen_credentials(), 'execute-api', 'ap-southeast-1').add_auth(request)
    with urllib.request.urlopen(urllib.request.Request(url, data=b'{}', headers=dict(request.headers.items()), method='POST'), timeout=30) as response:
        handoff = json.load(response)
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', handoff.get('code', '')):
        raise ValueError('Unexpected sign-in response')
    return ROOT + '#operator_code=' + handoff['code']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='default')
    args = parser.parse_args()
    url = login_url(args.profile)
    if not webbrowser.open(url):
        raise RuntimeError('Unable to open the browser. Check your default browser and rerun the helper.')
    print('Dashboard opened. Complete sign-in within two minutes; the link can be used once.')


if __name__ == '__main__':
    main()
