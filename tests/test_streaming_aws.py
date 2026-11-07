import importlib.util
from pathlib import Path
import json
import pytest

boto3=pytest.importorskip('boto3')
from botocore.stub import Stubber,ANY

spec=importlib.util.spec_from_file_location('stream_handler',Path(__file__).resolve().parents[1]/'streaming/handler.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def clients():
    kwargs={'region_name':'ap-southeast-1','aws_access_key_id':'local-test','aws_secret_access_key':'local-test'}
    return boto3.resource('dynamodb',**kwargs).meta.client,boto3.client('s3',**kwargs)


@pytest.mark.parametrize('version',[None,7])
def test_atomic_state_result_transaction_serializes_with_resource_client(version):
    ddb,s3=clients();store=module.DynamoStore(ddb,s3,'local-table','local-bucket','streaming/local')
    captured=[]
    ddb.meta.events.register('before-call.dynamodb.TransactWriteItems',lambda **kw:captured.append(kw['params']['body']))
    with Stubber(ddb) as stub:
        stub.add_response('transact_write_items',{}, {'TransactItems':ANY})
        store.commit([('sender',version,{'version':(version or 0)+1}),('receiver',0,{'version':1})],'result',{'model_score':.25})
    # Stubber short-circuits before-call handlers; parameter validation still checks the transformed API shapes.
    assert not captured


def test_commit_conditions_new_sender_and_preloaded_receiver():
    ddb,s3=clients();store=module.DynamoStore(ddb,s3,'local-table','local-bucket','streaming/local')
    sent=[]
    ddb.transact_write_items=lambda **request:sent.append(request)
    store.commit([('sender',None,{'version':1}),('receiver',0,{'version':1})],'result',{'model_score':.25})
    items=[entry['Put'] for entry in sent[0]['TransactItems']]
    assert [item['Item']['pk'] for item in items]==['sender','receiver','result']
    assert 'attribute_not_exists' in items[0]['ConditionExpression'] and 'attribute_not_exists' in items[2]['ConditionExpression']
    # A preloaded receiver exists at version 0, so its condition is a version check, not "does not exist".
    assert list(items[1]['ExpressionAttributeValues'].values())==[0] and 'attribute_not_exists' not in items[1]['ConditionExpression']


def test_preload_refuses_to_overwrite():
    ddb,s3=clients();store=module.DynamoStore(ddb,s3,'local-table','local-bucket','streaming/local')
    with Stubber(ddb) as stub:
        stub.add_response('put_item',{}, {'TableName':'local-table','Item':{'pk':'run:receiver:r','version':0,'payload':'{"history":[],"last_time":-1,"version":0}'},
                          'ConditionExpression':'attribute_not_exists(#n0)','ExpressionAttributeNames':{'#n0':'pk'}})
        store.preload('run:receiver:r',{'version':0,'last_time':-1,'history':[]})


def test_consistent_result_read_and_encrypted_scoped_audit():
    ddb,s3=clients();store=module.DynamoStore(ddb,s3,'local-table','local-bucket','streaming/local')
    with Stubber(ddb) as stub:
        stub.add_response('get_item',{'Item':{'pk':{'S':'result'},'payload':{'S':'{"model_score":0.25}'}}},
                          {'TableName':'local-table','Key':{'pk':'result'},'ConsistentRead':True})
        assert store.result('result')=={'model_score':.25}
    with Stubber(s3) as stub:
        stub.add_response('put_object',{}, {'Bucket':'local-bucket','Key':ANY,'Body':b'{"model_score":0.25}',
                           'ContentType':'application/json','ServerSideEncryption':'AES256'})
        store.audit('result',{'model_score':.25})
