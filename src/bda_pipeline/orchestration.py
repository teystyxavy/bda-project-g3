"""Offline definition renderer; never starts jobs or changes AWS resources."""
import json
import re
from pathlib import Path

ACCOUNT = "588738581042"
REGION = "ap-southeast-1"
ROWS = 5_078_345
INPUT_PREFIX = "curated/full-optimized-20261005/transactions"
ARTIFACT_PREFIX = "scoring-artifacts/frozen-localized-20261005"
MANIFEST = "323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f"


def definition_values(run_id, account=ACCOUNT, region=REGION):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,49}", run_id):
        raise ValueError("Use a safe 3–50-character run ID")
    if account != ACCOUNT or region != REGION:
        raise ValueError("This prepared workflow is scoped to Group 3's account/region")
    bucket = f"is459-g3-{account}-{region}"
    output = f"scored/{run_id}/transactions/"
    return {
        "run_id": run_id, "bucket": bucket, "rows": ROWS,
        "input_uri": f"s3://{bucket}/{INPUT_PREFIX}/",
        "output_prefix": output.rstrip("/"), "output_uri": f"s3://{bucket}/{output}",
        "marker_key": output + "_SUCCESS", "temp_prefix": f"tmp/scoring/{run_id}",
        "temp_uri": f"s3://{bucket}/tmp/scoring/{run_id}/",
        "script_uri": f"s3://{bucket}/{ARTIFACT_PREFIX}/score_transactions.py",
        "manifest_sha256": MANIFEST,
        "job_role": f"arn:aws:iam::{account}:role/is459-g3-glue-frozen-scoring",
        "notification_topic": f"arn:aws:sns:{region}:{account}:is459-g3-failures",
        "projection_template": f"s3://{bucket}/{INPUT_PREFIX}/txn_date=${{txn_date}}/",
        "athena_results": f"s3://{bucket}/athena-results/",
    }


def render_definition(repo_root, run_id):
    root = Path(repo_root)
    values = definition_values(run_id)
    values["query_sql"] = (root / "sql/full-orchestrated-corridors.sql").read_text(encoding="utf-8")
    template = (root / "infra/orchestration/workflow.asl.json.tftpl").read_text(encoding="utf-8")
    rendered = re.sub(r"\$\{([a-z0-9_]+)_json\}",
                      lambda match: json.dumps(values[match.group(1)]), template)
    if re.search(r"\$\{[a-z0-9_]+_json\}", rendered):
        raise ValueError("Unresolved definition placeholder")
    return json.loads(rendered)
