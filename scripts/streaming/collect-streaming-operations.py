"""Read-only Lambda logs, metrics and retained audit encryption evidence.

Purpose
-------
Operational evidence for the streaming demo, captured after the replay
(complements ``collect-streaming-evidence.py``, which checks the data). It
answers: did the Lambda consumer have runtime errors, throttles or record
failures; how much compute was billed (and roughly what it cost); and are
the S3 audit objects encrypted at rest?

AWS reads (read-only IAM):

* CloudWatch Logs ``/aws/lambda/is459-g3-stream-smoke-20261006`` - every log
  event (``logs:FilterLogEvents``);
* CloudWatch metrics ``AWS/Lambda`` Errors/Throttles/Invocations/Duration and
  the custom ``IS459/Streaming/stream-smoke-20261006`` RecordFailures metric
  for the last 2 hours at 1-minute resolution;
* ``s3:ListObjectsV2`` + ``s3:HeadObject`` on the first and last audit object
  under ``streaming/stream-smoke-20261006/audit/`` (encryption sample).

Output: a JSON report at ``--output`` (must not exist). Run it within about
2 hours of the replay, since the metric query window is relative to "now".

Usage::

    python scripts/streaming/collect-streaming-operations.py --output out/ops.json
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
from botocore.config import Config

# Lambda function / stream / table name of the single reviewed run.
NAME = 'is459-g3-stream-smoke-20261006'


def main():
    """Collect logs, metrics and encryption samples and write the report.

    Raises:
        ValueError: if the output exists or credentials belong to another account.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists(): raise ValueError('Use a fresh operations report')
    session = boto3.Session(profile_name='is459-g3-tools', region_name='ap-southeast-1')
    config = Config(retries={'total_max_attempts': 2, 'mode': 'standard'}, connect_timeout=3, read_timeout=20)
    if session.client('sts', config=config).get_caller_identity()['Account'] != '588738581042':
        raise ValueError('Wrong AWS account')
    # Single 'now' so every metric query uses the same 2-hour window.
    now = datetime.now(timezone.utc)
    logs = session.client('logs', config=config)
    # Read all log events for the function (paginated).
    events = [event for page in logs.get_paginator('filter_log_events').paginate(
        logGroupName='/aws/lambda/' + NAME) for event in page['events']]
    # Lambda writes one 'REPORT RequestId ... Billed Duration: N ms' line per invocation.
    reports = [event for event in events if event['message'].startswith('REPORT ')]
    # The shared handler prints {"stream_record_failed": 1, ...} when a record fails.
    failures = [event for event in events if '"stream_record_failed": 1' in event['message']]
    # Unhandled exceptions and timeouts are logged by the Lambda runtime like this.
    runtime_errors = [event for event in events if '[ERROR]' in event['message'] or 'Task timed out' in event['message']]
    # Total billed milliseconds across all invocations (parsed from REPORT lines).
    billed_ms = sum(float(re.search(r'Billed Duration:\s*([\d.]+) ms', event['message'])[1]) for event in reports)
    cloudwatch = session.client('cloudwatch', config=config)
    metrics = {}
    # Standard Lambda metrics, per minute, Sum and Maximum statistics.
    for name in ['Errors', 'Throttles', 'Invocations', 'Duration']:
        metrics[name] = cloudwatch.get_metric_statistics(Namespace='AWS/Lambda', MetricName=name,
            Dimensions=[{'Name': 'FunctionName', 'Value': NAME}],
            StartTime=now-timedelta(hours=2), EndTime=now, Period=60,
            Statistics=['Sum', 'Maximum'])['Datapoints']
    # Custom metric (from a log metric filter) used by the failure alarm.
    record_failures = cloudwatch.get_metric_statistics(Namespace='IS459/Streaming/stream-smoke-20261006',
        MetricName='RecordFailures', StartTime=now-timedelta(hours=2), EndTime=now, Period=60,
        Statistics=['Sum'])['Datapoints']
    # Sample only the first and last audit object (list_objects_v2 returns up to
    # 1,000 keys, enough for this 884-object run) to confirm SSE and versioning.
    s3 = session.client('s3', config=config)
    audit_list = s3.list_objects_v2(Bucket='is459-g3-588738581042-ap-southeast-1',
        Prefix='streaming/stream-smoke-20261006/audit/').get('Contents', [])
    encryption = []
    for obj in ([audit_list[0], audit_list[-1]] if audit_list else []):
        head = s3.head_object(Bucket='is459-g3-588738581042-ap-southeast-1', Key=obj['Key'])
        encryption.append({'key': obj['Key'], 'server_side_encryption': head.get('ServerSideEncryption'),
                           'version_id': head.get('VersionId'), 'bytes': head['ContentLength']})
    # Cost estimate = billed seconds x 2 GB memory x USD 0.0000166667 per GB-second
    # (Lambda x86 on-demand price). It deliberately excludes Kinesis, DynamoDB,
    # S3, CloudWatch and taxes, as the flag below states.
    report = {'captured_at': now.isoformat(), 'invocation_reports': len(reports),
        'billed_milliseconds': billed_ms, 'lambda_compute_usd_estimate': billed_ms/1000*2*0.0000166667,
        'estimate_excludes_other_services_and_taxes': True, 'record_failure_logs': failures,
        'runtime_error_logs': runtime_errors, 'lambda_metrics': metrics, 'record_failure_metric': record_failures,
        'audit_encryption_samples': encryption, 'logs': events}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=lambda value: value.isoformat())+'\n', encoding='utf-8')
    print(json.dumps({k: report[k] for k in ['invocation_reports', 'billed_milliseconds',
        'lambda_compute_usd_estimate']}))


if __name__ == '__main__': main()
