"""Forget predecessor deployment uploads through the initialized backend.

This changes Terraform state only. S3 objects remain available for recovery.
The caller must hold the ingestion application lock and review the next plan.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import subprocess
from datetime import datetime, timezone


BUCKET = 'is459-g3-588738581042-ap-southeast-1'
FILES = {'control.zip', 'bda_pipeline.zip', 'runtime.gluewheels.zip',
         'curate_transactions.py', 'score_transactions.py', 'pipeline_stage.py',
         'dashboard/index.html', 'dashboard/dashboard.js', 'dashboard/dashboard.css',
         *{'model/' + name for name in ['comparison.json', 'selection.json',
                                       'features.json', 'model-manifest.json', 'deployment.json']}}


def predecessor_addresses(state, prefix):
    if not re.fullmatch(r'pipeline/deployments/[0-9a-f]{64}', prefix):
        raise ValueError('Invalid immutable deployment prefix')
    addresses = []
    for resource in state.get('resources', []):
        if resource.get('type') != 'aws_s3_object' or resource.get('name') != 'deployment_artifact':
            continue
        if resource.get('module') or resource.get('mode') != 'managed':
            raise ValueError('Only root managed immutable uploads may be retained')
        for instance in resource['instances']:
            name = instance.get('index_key')
            attrs = instance['attributes']
            if (not isinstance(name, str) or
                    (name not in FILES and not re.fullmatch(r'model/[A-Za-z0-9_-]+\.ubj', name))):
                raise ValueError('Unexpected deployment artifact')
            key = attrs.get('key', '')
            if (attrs.get('bucket') != BUCKET or
                    not re.fullmatch(r'pipeline/deployments/[0-9a-f]{64}/' + re.escape(name), key)):
                raise ValueError('Artifact is outside the immutable deployment prefix')
            if key != prefix + '/' + name:
                addresses.append('aws_s3_object.deployment_artifact[' + json.dumps(name) + ']')
    return addresses


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform", default="terraform")
    parser.add_argument("--stack-directory", type=Path, required=True)
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()
    command = [args.terraform, f"-chdir={args.stack_directory.resolve()}"]
    # state pull/rm use the configured S3 backend and its DynamoDB lock.
    pulled = subprocess.check_output(command + ["state", "pull"])
    state = json.loads(pulled)
    addresses = predecessor_addresses(state, args.prefix)
    if not addresses:
        print("No predecessor deployment uploads need to be forgotten.")
        return
    backup_root = Path(os.environ["LOCALAPPDATA"]) / "IS459-G3/state-backups"
    repo = Path(__file__).resolve().parents[2]
    if backup_root.resolve().is_relative_to(repo):
        raise ValueError("State backups must remain outside the repository.")
    backup_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = backup_root / f"ingestion-pre-retain-{stamp}.tfstate"
    with backup.open("xb") as target:
        target.write(pulled)
    # Recheck after the backup; never knowingly operate on changed state.
    latest = json.loads(subprocess.check_output(command + ["state", "pull"]))
    if (latest["lineage"], latest["serial"]) != (state["lineage"], state["serial"]):
        raise RuntimeError("Shared state changed; retry after coordinating operators.")
    subprocess.run(
        command + ["state", "rm", "-lock-timeout=60s", *addresses], check=True
    )
    print(f"Retained predecessor S3 objects; private state backup: {backup}")


if __name__ == "__main__":
    main()
