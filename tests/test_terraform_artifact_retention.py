"""State retention must never select unrelated resources or active uploads."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "retain_artifacts",
    Path(__file__).resolve().parents[1] / "scripts/aws/retain-deployment-artifacts.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def resource(name, prefix, bucket="is459-g3-588738581042-ap-southeast-1"):
    return {
        "mode": "managed", "type": "aws_s3_object", "name": name,
        "instances": [{"index_key": "control.zip", "attributes": {
            "bucket": bucket, "key": prefix + "/control.zip",
        }}],
    }


def test_retains_only_predecessor_deployment_uploads():
    state = {"resources": [
        resource("deployment_artifact", "pipeline/deployments/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        resource("deployment_artifact", "pipeline/deployments/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"),
        resource("configuration", "pipeline"),
    ]}
    assert module.predecessor_addresses(state, "pipeline/deployments/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb") == [
        'aws_s3_object.deployment_artifact["control.zip"]'
    ]


@pytest.mark.parametrize("prefix", ["pipeline", "pipeline/deployments/../current", "pipeline/deployments/"])
def test_rejects_ambiguous_or_traversal_prefix(prefix):
    with pytest.raises(ValueError):
        module.predecessor_addresses({"resources": []}, prefix)


def test_refuses_to_forget_upload_in_another_bucket():
    with pytest.raises(ValueError, match="outside the immutable"):
        module.predecessor_addresses(
            {"resources": [resource("deployment_artifact", "pipeline/deployments/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "another-bucket")]},
            "pipeline/deployments/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )


@pytest.mark.parametrize('name', ['pipeline_stage.py', 'dashboard/index.html',
                                 'dashboard/dashboard.js', 'dashboard/dashboard.css'])
def test_retains_glue_stage_artifacts_on_future_updates(name):
    previous = 'pipeline/deployments/' + 'a' * 64
    current = 'pipeline/deployments/' + 'b' * 64
    item = resource('deployment_artifact', previous)
    instance = item['instances'][0]
    instance['index_key'] = name
    instance['attributes']['key'] = previous + '/' + name
    assert module.predecessor_addresses({'resources': [item]}, current) == [
        'aws_s3_object.deployment_artifact["' + name + '"]']
    assert module.predecessor_addresses({'resources': [item]}, previous) == []
