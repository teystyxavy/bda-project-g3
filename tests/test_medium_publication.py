"""Publication must preserve conditional writes with Glue's older API model."""
import importlib.util
from pathlib import Path

import boto3
import pytest

spec = importlib.util.spec_from_file_location('medium_evaluation', Path(__file__).parents[1] / 'jobs/medium_delta1_evaluation.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_condition_is_signed_without_modeled_parameter():
    client = boto3.client('s3', region_name='ap-southeast-1', aws_access_key_id='test', aws_secret_access_key='test')
    captured = []
    class StopRequest(Exception):
        pass
    def capture(request, **_):
        captured.append(request)
        raise StopRequest()
    client.meta.events.register('before-send.s3.PutObject', capture)
    with pytest.raises(StopRequest):
        module.put_immutable(client, Bucket='test-bucket', Key='release', Body=b'data')
    assert captured[0].headers['If-None-Match'] == b'*'
    assert b'if-none-match' in captured[0].headers['Authorization']
    with pytest.raises(StopRequest):
        client.put_object(Bucket='test-bucket', Key='other', Body=b'data')
    assert 'If-None-Match' not in captured[1].headers
