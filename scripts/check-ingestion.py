"""Read-only AWS prerequisites, quota, price and ASL validation. Never deploys."""
import argparse
import json
from pathlib import Path
import boto3
from botocore.config import Config
from bda_pipeline.ingest_contract import ACCOUNT, REGION, BUCKET, SITE_BUCKET


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='is459-g3-tools')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=REGION)
    sdk = Config(retries={'mode': 'standard', 'total_max_attempts': 3}, connect_timeout=10, read_timeout=30, user_agent_appid='AWSSkill-SageMaker')
    client = lambda name, region=REGION: session.client(name, region_name=region, config=sdk)
    if client('sts').get_caller_identity()['Account'] != ACCOUNT: raise ValueError('Wrong AWS account')
    report = {'account': ACCOUNT, 'region': REGION, 'read_only': True}
    for bucket in [BUCKET, SITE_BUCKET]: client('s3').head_bucket(Bucket=bucket)
    group = client('athena').get_work_group(WorkGroup='is459-g3-analysis')['WorkGroup']
    report['athena'] = {'state': group['State'], 'scan_limit_bytes': group['Configuration']['BytesScannedCutoffPerQuery']}
    if group['State'] != 'ENABLED' or report['athena']['scan_limit_bytes'] > 1_073_741_824: raise ValueError('Athena cost guard changed')
    found = []
    for page in client('service-quotas').get_paginator('list_service_quotas').paginate(ServiceCode='sagemaker'):
        for item in page['Quotas']:
            name = item['QuotaName'].lower()
            if ('ml.m5.2xlarge' in name and 'processing' in name) or ('ml.m5.xlarge' in name and 'training' in name and 'spot' not in name and 'warm' not in name):
                found.append({'name': item['QuotaName'], 'code': item['QuotaCode'], 'value': item['Value']})
        if len(found) == 2: break
    report['quotas'] = found
    report['quota_ready'] = len(found) == 2 and all(item['value'] >= 1 for item in found)
    report['hourly_prices_usd'] = {}
    pricing = client('pricing', 'us-east-1')
    for usage in ['APS1-Processing:ml.m5.2xlarge', 'APS1-Train:ml.m5.xlarge']:
        for page in pricing.get_paginator('get_products').paginate(ServiceCode='AmazonSageMaker', Filters=[{'Type': 'TERM_MATCH', 'Field': 'usagetype', 'Value': usage}]):
            for raw in page['PriceList']:
                product = json.loads(raw)
                for term in product['terms'].get('OnDemand', {}).values():
                    for dimension in term['priceDimensions'].values():
                        if dimension['unit'] == 'Hrs': report['hourly_prices_usd'][usage] = float(dimension['pricePerUnit']['USD'])
    root = Path(__file__).resolve().parents[1]
    definition = (root / 'infra/ingestion/workflow.asl.json').read_text().replace('${control_arn}', f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:is459-g3-ingest-control').replace('${failure_topic}', f'arn:aws:sns:{REGION}:{ACCOUNT}:is459-g3-failures')
    report['state_machine_validation'] = client('stepfunctions').validate_state_machine_definition(definition=definition, type='STANDARD', severity='WARNING')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if report['state_machine_validation']['result'] != 'OK': raise ValueError('ASL validation failed')


if __name__ == '__main__': main()
