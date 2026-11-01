"""Read-only account, S3, Athena, optional training quota/price and ASL checks.

Baseline and export use Glue G.2X; no SageMaker Processing quota is needed.
quota_ready refers only to optional ml.m5.xlarge candidate training. This script
never deploys or starts compute jobs.
"""
import argparse
import json
from pathlib import Path
import boto3
from botocore.config import Config
from bda_pipeline.ingest_contract import ACCOUNT, REGION, BUCKET, SITE_BUCKET


def main():
    """Run every read-only readiness check and write a JSON report.

    Command-line arguments:
        --profile: named AWS CLI profile to use (default ``is459-g3-tools``).
        --output: path of the JSON report file to create (parent folders are
            created if needed).

    Returns:
        None. The report is written to ``--output`` and echoed to stdout.

    Raises:
        ValueError: wrong AWS account, Athena workgroup disabled or its scan
            limit raised above 1 GiB, or the Step Functions definition does not
            validate as ``OK``.
        botocore errors: if a bucket/workgroup is missing or access is denied.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='is459-g3-tools')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    # All clients share one session pinned to the project region (ap-southeast-1).
    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    # Standard retry mode with at most 3 attempts and short timeouts: a read-only
    # check should fail fast rather than hang. The user-agent app id tags these
    # calls so they can be identified in CloudTrail.
    sdk = Config(retries={'mode': 'standard', 'total_max_attempts': 3}, connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    # Small factory so every client gets the same config; the region can be
    # overridden because the Pricing API only exists in us-east-1.
    client = lambda name, region=REGION: session.client(name, region_name=region, config=sdk)
    # Safety first: refuse to continue against any account other than the project's.
    if client('sts').get_caller_identity()['Account'] != ACCOUNT: raise ValueError('Wrong AWS account')
    report = {'account': ACCOUNT, 'region': REGION, 'read_only': True}
    # head_bucket raises (403/404) if either bucket is missing or not accessible.
    for bucket in [BUCKET, SITE_BUCKET]: client('s3').head_bucket(Bucket=bucket)
    # Athena cost guard: the workgroup must be enabled and must cap each query
    # at <= 1 GiB scanned (1_073_741_824 bytes), keeping corridor SQL cheap.
    group = client('athena').get_work_group(WorkGroup='is459-g3-analysis')['WorkGroup']
    report['athena'] = {'state': group['State'], 'scan_limit_bytes': group['Configuration']['BytesScannedCutoffPerQuery']}
    if group['State'] != 'ENABLED' or report['athena']['scan_limit_bytes'] > 1_073_741_824: raise ValueError('Athena cost guard changed')
    # Only optional training uses SageMaker quotas; baseline/export run in Glue.
    found = []
    for page in client('service-quotas').get_paginator('list_service_quotas').paginate(ServiceCode='sagemaker'):
        for item in page['Quotas']:
            name = item['QuotaName'].lower()
            if ('ml.m5.xlarge' in name and 'training' in name and 'spot' not in name and 'warm' not in name):
                found.append({'name': item['QuotaName'], 'code': item['QuotaCode'], 'value': item['Value']})
        # Stop paginating once the training quota is found (the list is long).
        if len(found) == 1: break
    report['quotas'] = found
    # "Ready" means the optional training quota allows at least one instance.
    # This is reported rather than enforced so the report can still be reviewed.
    report['quota_ready'] = len(found) == 1 and all(item['value'] >= 1 for item in found)
    report['hourly_prices_usd'] = {}
    # The AWS Pricing API is a global service served only from us-east-1.
    pricing = client('pricing', 'us-east-1')
    # APS1 = Asia Pacific (Singapore) usage-type prefix for the project region.
    for usage in ['APS1-Train:ml.m5.xlarge']:
        for page in pricing.get_paginator('get_products').paginate(ServiceCode='AmazonSageMaker', Filters=[{'Type': 'TERM_MATCH', 'Field': 'usagetype', 'Value': usage}]):
            # Each PriceList entry is a JSON *string*; drill into the OnDemand
            # terms and keep the per-hour ("Hrs") USD price dimension.
            for raw in page['PriceList']:
                product = json.loads(raw)
                for term in product['terms'].get('OnDemand', {}).values():
                    for dimension in term['priceDimensions'].values():
                        if dimension['unit'] == 'Hrs': report['hourly_prices_usd'][usage] = float(dimension['pricePerUnit']['USD'])
    # Repo root = two levels above scripts/ingestion/.
    root = Path(__file__).resolve().parents[2]
    # The ASL file contains Terraform template placeholders; substitute the real
    # ARNs of the control Lambda and the failure SNS topic so AWS can validate it.
    definition = (root / 'infra/ingestion/workflow.asl.json').read_text().replace('${control_arn}', f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:is459-g3-ingest-control').replace('${failure_topic}', f'arn:aws:sns:{REGION}:{ACCOUNT}:is459-g3-failures')
    # ValidateStateMachineDefinition is a free, read-only API; WARNING severity
    # reports warnings as well as errors.
    report['state_machine_validation'] = client('stepfunctions').validate_state_machine_definition(definition=definition, type='STANDARD', severity='WARNING')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    # Fail only after the report is saved so the validation diagnostics are kept.
    if report['state_machine_validation']['result'] != 'OK': raise ValueError('ASL validation failed')


if __name__ == '__main__': main()
