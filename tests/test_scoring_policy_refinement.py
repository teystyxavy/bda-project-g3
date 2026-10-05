"""Adversarial plan checks for the offline deployment-policy refiner."""
import importlib.util
import json
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "scoring_policy_refiner",
    Path(__file__).parents[1] / "scripts/refine-scoring-deployment-policy.py",
)
refiner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refiner)


@pytest.fixture
def plan():
    names = {
        'aws_cloudwatch_log_group.scoring["error"]': "/is459-g3/scoring/glue/error",
        'aws_cloudwatch_log_group.scoring["output"]': "/is459-g3/scoring/glue/output",
        "aws_iam_role.scoring[0]": "is459-g3-glue-frozen-scoring",
        "aws_iam_role.failure[0]": "is459-g3-scoring-failure-delivery",
        "aws_glue_job.scoring[0]": "is459-g3-score-frozen",
        "aws_cloudwatch_event_rule.failure[0]": "is459-g3-frozen-scoring-failures",
        "aws_iam_role_policy.scoring[0]": None,
        "aws_iam_role_policy.failure[0]": None,
        "aws_cloudwatch_event_target.failure[0]": None,
    }
    return {"variables": {"aws_account_id": {"value": "588738581042"},
                          "aws_region": {"value": "ap-southeast-1"}},
            "resource_changes": [{"address": a, "change": {
                "actions": ["create"], "after": {"name": n}}}
                for a, n in names.items()]}


@pytest.mark.parametrize("variable,value", [
    ("aws_account_id", "999999999999"), ("aws_region", "us-east-1")])
def test_reject_other_account_or_region(plan, variable, value):
    plan["variables"][variable]["value"] = value
    with pytest.raises(ValueError, match="approved account/region"):
        refiner.refine({}, plan, {})


@pytest.mark.parametrize("actions", [["delete"], ["update"], ["delete", "create"]])
def test_reject_cleanup_or_replacement(plan, actions):
    plan["resource_changes"][0]["change"]["actions"] = actions
    with pytest.raises(ValueError, match="create-only"):
        refiner.refine({}, plan, {})


def test_reject_additional_resource(plan):
    plan["resource_changes"].append({"address": "aws_iam_role.unrelated"})
    with pytest.raises(ValueError, match="nine-resource"):
        refiner.refine({}, plan, {})


def test_reject_renamed_resource(plan):
    plan["resource_changes"][0]["change"]["after"]["name"] = "/unrelated"
    with pytest.raises(ValueError, match="resource name changed"):
        refiner.refine({}, plan, {})


def test_reject_service_only_trust(plan):
    scoring = next(c for c in plan["resource_changes"] if c["address"] == "aws_iam_role.scoring[0]")
    scoring["change"]["after"]["assume_role_policy"] = json.dumps({"Statement": [{
        "Effect": "Allow", "Principal": {"Service": "glue.amazonaws.com"},
        "Action": "sts:AssumeRole"}]})
    with pytest.raises(ValueError, match="strict Glue trust changed"):
        refiner.refine({}, plan, {})
