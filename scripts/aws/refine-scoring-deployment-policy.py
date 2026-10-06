"""Narrow an Autopilot baseline for the reviewed, create-only scoring plan.

Offline only: never attaches a policy, uploads artifacts, or invokes AWS.
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def refine(baseline, plan, references):
    account = plan["variables"]["aws_account_id"]["value"]
    region = plan["variables"]["aws_region"]["value"]
    if account != "588738581042" or region != "ap-southeast-1":
        raise ValueError("This review is restricted to the approved account/region")
    expected = {
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
    changes = plan["resource_changes"]
    if {c["address"] for c in changes} != set(expected) or len(changes) != 9:
        raise ValueError("Resource set differs from the reviewed nine-resource plan")
    for change in changes:
        if change["change"]["actions"] != ["create"]:
            raise ValueError("Only the reviewed create-only plan is supported")
        name = expected[change["address"]]
        if name and change["change"]["after"]["name"] != name:
            raise ValueError("A reviewed resource name changed")
    # Confirm that trust has not been weakened in the plan used for refinement.
    scoring = next(c for c in changes if c["address"] == "aws_iam_role.scoring[0]")
    trust = json.loads(scoring["change"]["after"]["assume_role_policy"])
    job = f"arn:aws:glue:{region}:{account}:job/is459-g3-score-frozen"
    if trust["Statement"] != [{
        "Effect": "Allow", "Principal": {"Service": "glue.amazonaws.com"},
        "Action": "sts:AssumeRole", "Condition": {
            "StringEquals": {"aws:SourceAccount": account},
            "ArnEquals": {"aws:SourceArn": job},
        },
    }]:
        raise ValueError("The reviewed strict Glue trust changed")
    policies = baseline["Policies"]
    if len(policies) != 1 or policies[0]["PolicyType"] != "Identity":
        raise ValueError("Expected one Autopilot identity-policy baseline")
    statements = policies[0]["Policy"]["Statement"]
    if any(s["Effect"] != "Allow" or "NotAction" in s for s in statements):
        raise ValueError("Unexpected baseline statement shape")
    available = {a for s in statements for a in s["Action"]}
    roles = [f"arn:aws:iam::{account}:role/{name}" for name in (
        "is459-g3-glue-frozen-scoring", "is459-g3-scoring-failure-delivery")]
    rule = f"arn:aws:events:{region}:{account}:rule/is459-g3-frozen-scoring-failures"
    log_groups = [f"arn:aws:logs:{region}:{account}:log-group:/is459-g3/scoring/glue/{s}"
                  for s in ("error", "output")]
    # Every action is selected from the generated baseline, never invented.
    specifications = [
        ("ScoringRoles", ["iam:CreateRole", "iam:GetRole", "iam:GetRolePolicy",
         "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole",
         "iam:ListRolePolicies", "iam:ListRoleTags", "iam:PutRolePolicy",
         "iam:TagRole"], roles, "role"),
        ("PassScoringRole", ["iam:PassRole"], [roles[0]], "role"),
        ("PassFailureRole", ["iam:PassRole"], [roles[1]], "role"),
        ("FrozenJob", ["glue:CreateJob", "glue:GetJob", "glue:GetTags",
         "glue:TagResource"], [job], "job"),
        ("ReadJobCreationUsageProfiles", ["glue:GetUsageProfile"],
         [f"arn:aws:glue:{region}:{account}:usageProfile/*"], "usageProfile"),
        ("FailureRule", ["events:DescribeRule", "events:ListTargetsByRule",
         "events:PutRule", "events:PutTargets", "events:ListTagsForResource",
         "events:TagResource"], [rule], "rule-on-default-event-bus"),
        ("ScoringLogGroups", ["logs:CreateLogGroup", "logs:PutRetentionPolicy",
         "logs:TagLogGroup"], [g + ":*" for g in log_groups], "log-group"),
        ("ScoringLogTags", ["logs:ListTagsForResource", "logs:TagResource"],
         log_groups, "log-group"),
        ("DiscoverLogGroups", ["logs:DescribeLogGroups"], ["*"], None),
    ]
    result = []
    for sid, actions, resources, resource_type in specifications:
        if not set(actions) <= available:
            raise ValueError(f"Baseline lacks required selected actions for {sid}")
        for action in actions:
            service, name = action.split(":")
            definition = next(a for a in references[service]["Actions"] if a["Name"] == name)
            supported = {r["Name"] for r in definition.get("Resources", [])}
            if resource_type and resource_type not in supported:
                raise ValueError(f"AWS reference does not support scope for {action}")
            if not resource_type and supported:
                raise ValueError(f"Unnecessary wildcard scope for {action}")
        statement = {"Sid": sid, "Effect": "Allow", "Action": actions, "Resource": resources}
        if sid.startswith("Pass"):
            service = "glue.amazonaws.com" if sid == "PassScoringRole" else "events.amazonaws.com"
            statement["Condition"] = {"StringEquals": {"iam:PassedToService": service}}
        result.append(statement)
    selected = {a for s in result for a in s["Action"]}
    return {"Version": "2012-10-17", "Statement": result}, sorted(available - selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference_paths = {s: args.references / f"{s}-reference.json" for s in ("iam", "events", "logs", "glue")}
    load = lambda p: json.loads(p.read_text(encoding="utf-8-sig"))
    policy, removed = refine(load(args.baseline), load(args.plan), {s: load(p) for s, p in reference_paths.items()})
    if args.output.exists() or args.output.with_suffix(".review.json").exists():
        raise ValueError("Use a fresh output file; previous reviews are preserved")
    provenance = {"baseline_sha256": digest(args.baseline), "plan_sha256": digest(args.plan),
                  "reference_sha256": {s: digest(p) for s, p in reference_paths.items()},
                  "removed_actions": removed, "status": "candidate-not-attached-not-cloud-tested",
                  "remaining_wildcard": "logs:DescribeLogGroups uses Resource *; usageProfile/* is read-only",
                  "log_arn_suffix": "Named log groups use :* for non-tag actions, without :* for modern tag APIs"}
    args.output.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    args.output.with_suffix(".review.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"statements": len(policy["Statement"]), "removed_actions": len(removed),
                      "wildcard_statements": sum(s["Resource"] == ["*"] for s in policy["Statement"])}))


if __name__ == "__main__":
    main()
