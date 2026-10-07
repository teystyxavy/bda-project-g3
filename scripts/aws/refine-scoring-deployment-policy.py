"""Narrow an Autopilot baseline for the reviewed, create-only scoring plan.

Offline only: never attaches a policy, uploads artifacts, or invokes AWS.

Why this exists (least-privilege IAM)
-------------------------------------
Deploying the frozen-model Glue scoring stack (``infra/scoring``) with
Terraform needs an IAM policy for the *deployer*. IAM Access Analyzer /
"Autopilot"-style policy generation gives a broad *baseline* of every action
that might be needed. This script turns that baseline into a narrow policy
that allows only what the reviewed Terraform plan actually creates, scoped
to the exact resource ARNs:

* two IAM roles (Glue scoring role, EventBridge failure-delivery role) and
  ``iam:PassRole`` for each, restricted with ``iam:PassedToService`` to the
  one AWS service allowed to use it;
* the Glue job ``is459-g3-score-frozen`` (+ read-only usage profiles);
* the EventBridge rule ``is459-g3-frozen-scoring-failures`` and its target;
* the two CloudWatch log groups ``/is459-g3/scoring/glue/{error,output}``;
* ``logs:DescribeLogGroups`` on ``*`` (that API has no resource-level scope).

Safety checks before writing anything
-------------------------------------
* account/region are the approved project ones;
* the Terraform plan JSON contains exactly the nine reviewed resources, all
  ``create`` only, with the reviewed names;
* the Glue role trust policy is still the strict one (Glue service, this
  account, this job ARN only);
* each selected action exists in the baseline (never invented) and, per the
  AWS service-authorization reference JSON, supports the resource type it is
  scoped to (or truly supports only ``*``).

Inputs / outputs
----------------
* ``--baseline``: generated baseline policy JSON (one identity policy).
* ``--plan``: ``terraform show -json`` output of the reviewed plan.
* ``--references``: folder with ``{iam,events,logs,glue}-reference.json``.
* ``--output``: new policy file; a ``.review.json`` provenance file (input
  hashes, actions removed from the baseline, remaining wildcards) is written
  beside it. Existing files are never overwritten.

Usage
-----
    python scripts/aws/refine-scoring-deployment-policy.py --baseline baseline.json \
        --plan plan.json --references refs/ --output output/scoring-deployer-policy.json
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    """Return the SHA-256 hex digest of a file (used for provenance records).
    """
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def refine(baseline, plan, references):
    """Select a least-privilege policy from ``baseline`` for the reviewed ``plan``.

    Args:
        baseline: parsed baseline JSON with a ``Policies`` list containing one
            identity policy of ``Allow`` statements.
        plan: parsed ``terraform show -json`` plan for ``infra/scoring``.
        references: dict service -> parsed AWS service reference JSON (lists
            each action and the resource types it supports).

    Returns:
        ``(policy, removed)``: the refined IAM policy document, and a sorted
        list of baseline actions that were *not* granted (for the review log).

    Raises:
        ValueError: on any deviation from the reviewed plan, trust policy or
            baseline shape, or if an action cannot be scoped as intended.
    """
    account = plan["variables"]["aws_account_id"]["value"]
    region = plan["variables"]["aws_region"]["value"]
    if account != "588738581042" or region != "ap-southeast-1":
        raise ValueError("This review is restricted to the approved account/region")
    # Terraform address -> expected resource name (None = no name to check,
    # e.g. inline policies and event targets).
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
    # The baseline must be a single identity policy with plain Allow
    # statements; NotAction would grant "everything except", which we can't
    # narrow safely.
    policies = baseline["Policies"]
    if len(policies) != 1 or policies[0]["PolicyType"] != "Identity":
        raise ValueError("Expected one Autopilot identity-policy baseline")
    statements = policies[0]["Policy"]["Statement"]
    if any(s["Effect"] != "Allow" or "NotAction" in s for s in statements):
        raise ValueError("Unexpected baseline statement shape")
    # All actions the baseline offers; the refined policy is a subset of this.
    available = {a for s in statements for a in s["Action"]}
    roles = [f"arn:aws:iam::{account}:role/{name}" for name in (
        "is459-g3-glue-frozen-scoring", "is459-g3-scoring-failure-delivery")]
    rule = f"arn:aws:events:{region}:{account}:rule/is459-g3-frozen-scoring-failures"
    log_groups = [f"arn:aws:logs:{region}:{account}:log-group:/is459-g3/scoring/glue/{s}"
                  for s in ("error", "output")]
    # Every action is selected from the generated baseline, never invented.
    # (Sid, actions, resource ARNs, resource type the actions must support).
    # Log-group ARNs use ':*' for classic APIs but bare ARNs for the newer
    # tagging APIs, which is why the log statements are split in two.
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
        # Each action must be in the baseline and must support the chosen
        # resource type in AWS's reference; a None type means "only valid with
        # Resource *", which is accepted only if the action has no resource types.
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
        # PassRole lets the deployer hand a role to a service; the condition
        # limits each role to its intended service (Glue or EventBridge) so it
        # cannot be passed to, e.g., EC2 or Lambda.
        if sid.startswith("Pass"):
            service = "glue.amazonaws.com" if sid == "PassScoringRole" else "events.amazonaws.com"
            statement["Condition"] = {"StringEquals": {"iam:PassedToService": service}}
        result.append(statement)
    selected = {a for s in result for a in s["Action"]}
    return {"Version": "2012-10-17", "Statement": result}, sorted(available - selected)


def main():
    """Load inputs, refine the policy and write the policy + review files.

    Raises:
        ValueError: if the output or its ``.review.json`` already exists, or
            any check in :func:`refine` fails.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reference_paths = {s: args.references / f"{s}-reference.json" for s in ("iam", "events", "logs", "glue")}
    # utf-8-sig tolerates a BOM, which PowerShell/AWS CLI output files may have.
    load = lambda p: json.loads(p.read_text(encoding="utf-8-sig"))
    policy, removed = refine(load(args.baseline), load(args.plan), {s: load(p) for s, p in reference_paths.items()})
    if args.output.exists() or args.output.with_suffix(".review.json").exists():
        raise ValueError("Use a fresh output file; previous reviews are preserved")
    # Provenance: input hashes, what was removed, and the known remaining
    # wildcards, so a reviewer can audit how the policy was derived.
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
