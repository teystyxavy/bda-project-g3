"""AWS adapter: durable deduplication/state, replay-scoped encrypted audit.

Where this sits in the pipeline
-------------------------------
Entry point of the optional streaming demo Lambda (container image built
from ``streaming/Dockerfile``; Terraform in ``infra/streaming``). Flow::

    replay script -> Kinesis Data Stream -> this Lambda -> DynamoDB (state +
    results) and S3 (encrypted audit JSON)

The cloud-independent logic (validation, rolling features, idempotency,
batch failure handling) is in ``bda_pipeline/scoring/streaming.py``. This file only
adds the AWS pieces:

* ``DynamoStore``: the ``MemoryStore`` interface implemented on DynamoDB
  (table key ``pk``) and S3.
* ``runtime``: builds AWS clients and loads the frozen model once per
  Lambda container.
* ``handler``: the function Lambda calls (``CMD ["handler.handler"]``).

Environment variables (set by Terraform): ``STATE_TABLE`` (DynamoDB table),
``AUDIT_BUCKET`` (S3 bucket), ``RUN_ID`` (replay run, also S3 prefix
``streaming/<RUN_ID>/``) and ``MANIFEST_SHA256`` (hash of the frozen model
manifest baked into the image under ``/var/task/artifacts``).
"""
import json
from functools import lru_cache
import os

from bda_pipeline.scoring.streaming import canonical, fingerprint, handle_records
from bda_pipeline.scoring.inference import score_frame, worker_ranker


class DynamoStore:
    """DynamoDB/S3 implementation of the store used by ``process_event``.

    Sender states and score results share one table; their ``pk`` values are
    namespaced (``<run>:sender:<hash>`` and ``<run>:result:<hash>``) by
    ``process_event``. Each item stores the canonical JSON in ``payload``.
    """
    def __init__(self,ddb,s3,table,bucket,prefix):
        """Keep the low-level DynamoDB client, S3 client, table, bucket and S3 prefix."""
        self.ddb,self.s3,self.table,self.bucket,self.prefix=ddb,s3,table,bucket,prefix

    def _get(self,key):
        """Read one item by ``pk`` and return its parsed payload (or None).

        ConsistentRead=True: always see the latest committed write. Without it
        a retried record could read stale state and recompute features from
        an older history.
        """
        item=self.ddb.get_item(TableName=self.table,Key={'pk':key},ConsistentRead=True).get('Item')
        return json.loads(item['payload']) if item else None

    # Results and states are read the same way, so both names use _get.
    result=_get
    state=_get

    def commit(self,key,old_version,state,result_key,result):
        """Write the new sender state and the result in one DynamoDB transaction.

        Conditions (the whole transaction fails if either is false):
            * state item: its stored ``version`` still equals ``old_version``
              (or, for a new sender, the item does not exist yet). This is
              optimistic locking: a concurrent update makes us fail and retry
              instead of overwriting.
            * result item: must not exist yet, so a transaction is scored once.
        """
        from boto3.dynamodb.conditions import Attr,ConditionExpressionBuilder
        def condition(expression):
            """Turn a boto3 condition object into the raw request fields."""
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
        """Write the result as JSON to ``s3://<bucket>/<prefix>/audit/<hash>.json``.

        The object key is a hash of the result key, so a redelivered record
        simply overwrites the same object with the same content.
        ServerSideEncryption='AES256' requests SSE-S3 encryption at rest.
        """
        body=canonical(result).encode()
        self.s3.put_object(Bucket=self.bucket,Key=self.prefix+'/audit/'+fingerprint(key)+'.json',
                           Body=body,ContentType='application/json',ServerSideEncryption='AES256')


@lru_cache(maxsize=1)
def runtime():
    """Create AWS clients, the store and a predict function, once per container.

    ``lru_cache`` keeps the result for later invocations of the same warm
    Lambda container, so the model and clients are only loaded on a cold
    start.

    Returns:
        (store, predict) where ``predict(txn_dict)`` returns one model score.
    """
    import boto3
    from botocore.config import Config
    import pandas as pd
    # Few retries and short timeouts: fail fast and let the Kinesis retry /
    # failure destination handle the record, rather than hanging until the
    # Lambda timeout.
    config=Config(retries={'total_max_attempts':2,'mode':'standard'},connect_timeout=3,read_timeout=10)
    # Resource client automatically marshals native values and condition builders, including transactions.
    ddb=boto3.resource('dynamodb',config=config).meta.client
    store=DynamoStore(ddb,boto3.client('s3',config=config),os.environ['STATE_TABLE'],os.environ['AUDIT_BUCKET'],
                      'streaming/'+os.environ['RUN_ID'])
    # Verifies every model file against the pinned manifest hash, then loads
    # the XGBoost model with one thread (one transaction at a time).
    ranker=worker_ranker('/var/task/artifacts',os.environ['MANIFEST_SHA256'],threads=1)
    def predict(txn):
        """Score one transaction dict (raw fields + computed features)."""
        frame=pd.DataFrame([txn]);frame['timestamp']=pd.to_datetime(frame.timestamp)
        # score_frame applies the same input checks as the batch Glue job.
        return score_frame(frame,ranker).model_score.iloc[0]
    return store,predict


def handler(event,context):
    """Lambda entry point for a batch of Kinesis records.

    Returns the ``batchItemFailures`` response from ``handle_records`` so
    Lambda retries only from the first failed record onward.
    """
    store,predict=runtime()
    return handle_records(event,os.environ['RUN_ID'],store,predict)
