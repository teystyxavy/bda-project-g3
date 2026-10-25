"""Offline definition renderer; never starts jobs or changes AWS resources.

Where this sits in the pipeline
-------------------------------
The full batch run is orchestrated by an AWS Step Functions state machine
(``is459-g3-reviewed-batch``, Terraform in ``infra/orchestration``). Its
Amazon States Language (ASL) definition is a template,
``infra/orchestration/workflow.asl.json.tftpl``, with placeholders such as
``${bucket_json}``. At a high level the workflow runs the frozen-model Glue
scoring job (BQ1) and the Athena corridor query
``sql/full-orchestrated-corridors.sql`` (BQ2), and notifies an SNS topic on
failure.

This module fills in the same placeholders in Python so the team can render
and review the exact JSON definition locally, without Terraform and without
touching AWS. It is used by:

* ``scripts/aws/prepare-orchestration.py --run-id <id> --output <dir>``,
  which writes ``definition.json`` and an unapproved ``execution-input.json``;
* the streaming scripts, which import ``MANIFEST`` (the frozen model hash).

The constants below must stay in sync with ``infra/orchestration/main.tf``
(same bucket naming, prefixes, row count and manifest hash).
"""
import json
import re
from pathlib import Path

# The Group 3 AWS account and region; the workflow is deliberately locked to
# them so a rendered definition cannot be pointed at another account.
ACCOUNT = "588738581042"
REGION = "ap-southeast-1"
# Number of rows in the Kaggle HI-Small transactions file; the Glue scoring
# job must read and write exactly this many.
ROWS = 5_078_345
# S3 key prefix of the curated Parquet (written by jobs/curate_transactions.py).
INPUT_PREFIX = "curated/full-optimized-20261005/transactions"
# S3 key prefix holding score_transactions.py and the frozen model artifacts.
ARTIFACT_PREFIX = "scoring-artifacts/frozen-localized-20261005"
# SHA-256 of model-manifest.json: pins the exact frozen model the job may use.
MANIFEST = "323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f"


def definition_values(run_id, account=ACCOUNT, region=REGION):
    """Compute every value substituted into the ASL template for one run.

    Args:
        run_id: short lowercase run name (3-50 chars of a-z, 0-9 and '-',
            starting with a letter or digit). It becomes part of S3 keys, so
            the strict pattern prevents unsafe paths like ``../``.
        account / region: must equal the project's account and region.

    Returns:
        dict of placeholder name -> value: bucket, expected rows, input /
        output / temp S3 URIs, ``_SUCCESS`` marker key, scoring script URI,
        manifest hash, Glue job role ARN, SNS failure topic ARN, Athena
        partition-projection template and Athena results location.

    Raises:
        ValueError: on an unsafe run ID or a different account/region.
    """
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,49}", run_id):
        raise ValueError("Use a safe 3–50-character run ID")
    if account != ACCOUNT or region != REGION:
        raise ValueError("This prepared workflow is scoped to Group 3's account/region")
    # Bucket naming convention used across all project stacks.
    bucket = f"is459-g3-{account}-{region}"
    # Each run writes to its own scored/<run_id>/ prefix so runs never
    # overwrite each other (the Glue job also refuses an existing prefix).
    output = f"scored/{run_id}/transactions/"
    return {
        "run_id": run_id, "bucket": bucket, "rows": ROWS,
        "input_uri": f"s3://{bucket}/{INPUT_PREFIX}/",
        "output_prefix": output.rstrip("/"), "output_uri": f"s3://{bucket}/{output}",
        # Spark writes this empty file only after a fully successful write.
        "marker_key": output + "_SUCCESS", "temp_prefix": f"tmp/scoring/{run_id}",
        "temp_uri": f"s3://{bucket}/tmp/scoring/{run_id}/",
        "script_uri": f"s3://{bucket}/{ARTIFACT_PREFIX}/score_transactions.py",
        "manifest_sha256": MANIFEST,
        "job_role": f"arn:aws:iam::{account}:role/is459-g3-glue-frozen-scoring",
        "notification_topic": f"arn:aws:sns:{region}:{account}:is459-g3-failures",
        # "${{txn_date}}" renders as the literal ${txn_date}: an Athena
        # partition-projection placeholder, filled in by Athena at query time.
        "projection_template": f"s3://{bucket}/{INPUT_PREFIX}/txn_date=${{txn_date}}/",
        "athena_results": f"s3://{bucket}/athena-results/",
    }


def render_definition(repo_root, run_id):
    """Render the Step Functions ASL template into a Python dict.

    Args:
        repo_root: repository root (to find the SQL file and the template).
        run_id: run name passed to ``definition_values``.

    Returns:
        The parsed state machine definition (dict), ready to be dumped as JSON
        for review.

    How substitution works: each ``${name_json}`` placeholder is replaced by
    ``json.dumps(values[name])``, i.e. a correctly quoted/escaped JSON value.
    This mirrors Terraform's ``templatefile`` + ``jsonencode`` in
    ``infra/orchestration/main.tf``, so both renderings produce the same JSON.
    The corridor SQL text is embedded so the query Athena runs is exactly the
    reviewed file.

    Raises:
        ValueError: if any placeholder is left unresolved.
    """
    root = Path(repo_root)
    values = definition_values(run_id)
    values["query_sql"] = (root / "sql/full-orchestrated-corridors.sql").read_text(encoding="utf-8")
    template = (root / "infra/orchestration/workflow.asl.json.tftpl").read_text(encoding="utf-8")
    rendered = re.sub(r"\$\{([a-z0-9_]+)_json\}",
                      lambda match: json.dumps(values[match.group(1)]), template)
    # Safety net: a leftover placeholder would produce invalid ASL.
    if re.search(r"\$\{[a-z0-9_]+_json\}", rendered):
        raise ValueError("Unresolved definition placeholder")
    # json.loads also proves the rendered result is valid JSON.
    return json.loads(rendered)
