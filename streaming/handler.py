"""AWS adapter: durable deduplication/state, replay-scoped encrypted audit."""
import json
from functools import lru_cache
import os

from bda_pipeline.streaming import canonical, fingerprint, handle_records
from bda_pipeline.inference import score_frame, worker_ranker


class DynamoStore:
    def __init__(self,ddb,s3,table,bucket,prefix):
        self.ddb,self.s3,self.table,self.bucket,self.prefix=ddb,s3,table,bucket,prefix

    def _get(self,key):
        item=self.ddb.get_item(TableName=self.table,Key={'pk':key},ConsistentRead=True).get('Item')
        return json.loads(item['payload']) if item else None

    result=_get
    state=_get

    def commit(self,key,old_version,state,result_key,result):
        from boto3.dynamodb.conditions import Attr,ConditionExpressionBuilder
        def condition(expression):
            # boto3 resource injection cannot scope nested transaction conditions correctly.
            built=ConditionExpressionBuilder().build_expression(expression)
            result={'ConditionExpression':built.condition_expression,
                    'ExpressionAttributeNames':built.attribute_name_placeholders}
            if built.attribute_value_placeholders:
                result['ExpressionAttributeValues']=built.attribute_value_placeholders
            return result
        self.ddb.transact_write_items(TransactItems=[
            {'Put':{'TableName':self.table,'Item':{'pk':key,'version':state['version'],'payload':canonical(state)},
                    **condition(Attr('version').eq(old_version) if old_version else Attr('pk').not_exists())}},
            {'Put':{'TableName':self.table,'Item':{'pk':result_key,'payload':canonical(result)},
                    **condition(Attr('pk').not_exists())}}
        ])

    def audit(self,key,result):
        body=canonical(result).encode()
        self.s3.put_object(Bucket=self.bucket,Key=self.prefix+'/audit/'+fingerprint(key)+'.json',
                           Body=body,ContentType='application/json',ServerSideEncryption='AES256')


@lru_cache(maxsize=1)
def runtime():
    import boto3
    from botocore.config import Config
    import pandas as pd
    config=Config(retries={'total_max_attempts':2,'mode':'standard'},connect_timeout=3,read_timeout=10)
    # Resource client automatically marshals native values and condition builders, including transactions.
    ddb=boto3.resource('dynamodb',config=config).meta.client
    store=DynamoStore(ddb,boto3.client('s3',config=config),os.environ['STATE_TABLE'],os.environ['AUDIT_BUCKET'],
                      'streaming/'+os.environ['RUN_ID'])
    ranker=worker_ranker('/var/task/artifacts',os.environ['MANIFEST_SHA256'],threads=1)
    def predict(txn):
        frame=pd.DataFrame([txn]);frame['timestamp']=pd.to_datetime(frame.timestamp)
        return score_frame(frame,ranker).model_score.iloc[0]
    return store,predict


def handler(event,context):
    store,predict=runtime()
    return handle_records(event,os.environ['RUN_ID'],store,predict)
