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


@pytest.mark.parametrize('version',[0,7])
def test_atomic_state_result_transaction_serializes_with_resource_client(version):
    ddb,s3=clients();store=module.DynamoStore(ddb,s3,'local-table','local-bucket','streaming/local')
    captured=[]
    ddb.meta.events.register('before-call.dynamodb.TransactWriteItems',lambda **kw:captured.append(kw['params']['body']))
    with Stubber(ddb) as stub:
        stub.add_response('transact_write_items',{}, {'TransactItems':ANY})
        store.commit('state',version,{'version':version+1},'result',{'model_score':.25})
    # Stubber short-circuits before-call handlers; parameter validation still checks the transformed API shapes.
    assert not captured


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
