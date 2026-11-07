# Deploying BQ2 mule-network detection to the ingestion stack

**Audience:** whoever runs the next `infra/ingestion` update (Windows machine,
Docker Desktop, `is459-g3-tools` AWS profile).

PRs #5 and #6 merged the Glue-based mule-network job (replacing the Athena
treasury-corridor analysis) into `main`. As of this runbook, **none of it is
deployed** — the live stack's `pipeline/deployment.json` still has
`stage_versions: {curated, scored}` (no `network` key) and there is no
`is459-g3-ingest-network` Glue job in the account. This is a normal
`update-ingestion.ps1` update, not a fresh `deploy-ingestion.ps1`.

## Already done (don't redo these)

- [x] `main` has 351 tests passing locally (`pytest -q --ignore=tests/spark`).
- [x] `pipeline/lock.json` from the unrelated 20261105-084819-4f4eae96 HI-Large
      training failure (predates this merge) has been released via the
      control Lambda's `recover` op. The pipeline is idle.
- [x] PR #7 (`fix/update-ingestion-network-allowlist`) fixes
      `scripts/aws/update-ingestion.ps1`: its Terraform-plan allowlist only
      knew about `curate`/`score`/`baseline`/`export`. Without it, the first
      plan after this merge aborts with `"Unreviewed or destructive change:
      aws_glue_job.job[\"network\"] create"` before touching AWS.
      **Merge PR #7 before running an update.**

## Expected Terraform plan

Only these resources should appear as planned changes:

| Address | Action |
|---|---|
| `aws_glue_job.job["network"]` | create |
| `aws_cloudwatch_log_group.glue["network/error"]` | create |
| `aws_cloudwatch_log_group.glue["network/output"]` | create |
| `aws_iam_role_policy.glue` | update (new `pipeline/runs/*/network/*` S3 grants) |
| `aws_iam_role_policy.workflow` | update (Athena query grants removed) |
| `aws_iam_role_policy.control` | update (Athena grants removed; network cache-copy PutObject added) |
| `aws_sfn_state_machine.ingest` | update (new `ReuseNetwork`/`Network`/`NetworkReused` branch replaces `Corridors`) |
| `aws_lambda_function.control` | update (new control code) |
| `aws_s3_object.configuration` | update (new `stage_versions.network`) |
| plus the usual new content-addressed `aws_s3_object.deployment_artifact[...]` creates |

If anything else shows up (a delete, a replace, or a resource not in this
list), stop and investigate before applying — don't just widen the allowlist
to make it pass.

## Steps

1. **Merge PR #7** (allowlist fix) into `main` first.
2. On the Windows machine, pull `main` and confirm `git log -1` shows PR #7's
   merge commit.
3. Build fresh artifacts (reuses the currently-deployed model/wheels/image —
   this is an infra-only change, not a retrain):
   ```powershell
   python scripts/aws/prepare-ingestion.py --output output/network-deploy-<date> `
     --models <path to the currently deployed model dir> `
     --wheels <path to runtime.gluewheels.zip> `
     --image-uri 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@sha256:653951a13249c020d22e1d67ede14dd1f8aef8ea781848facdd3daf639688f3a
   ```
   (That digest is the one in the currently-deployed `deployment.json` — confirm
   it's still current before using it.)
4. Dry-run the update (no `-Execute`):
   ```powershell
   .\scripts\aws\update-ingestion.ps1 -PreparedDirectory output/network-deploy-<date> -Image <local image tag>
   ```
   This validates artifacts, checks the pipeline lock, runs `terraform plan`,
   and checks the plan against the allowlist above. It writes
   `output/network-deploy-<date>/deployment-changes.json` — **read it** and
   confirm it matches the table above before proceeding.
5. **Review `terraform plan` output yourself**, not just the allowlist check —
   the allowlist only guards against unreviewed/destructive changes, it
   doesn't confirm the arguments are right. Specifically check the `network`
   Glue job's `script_location` points at `network_mules.py` and its
   `non_overridable_arguments` contain only the 3 common arguments (job
   bookmarks disabled, the log group, and the `_SUCCESS` marker). There must
   be no `--STAGE` or `--extra-py-files`; it reads `--OUTPUT`/`--RUN_ID` etc.
   from the Step Functions call, not baked-in stage args.
6. If the dry run is clean, re-run with `-Execute` to push the image (if
   changed) and apply:
   ```powershell
   .\scripts\aws\update-ingestion.ps1 -PreparedDirectory output/network-deploy-<date> -Image <local image tag> -Execute
   ```

## Cost note before the next real run

`bda_pipeline.zip` now bundles `network.py`, so its hash changes, which
changes the `scored` stage-version even though `score_transactions.py` itself
didn't change. **The first run after this deploy cannot reuse any previously
cached `scored` output** — it will re-score from curated Parquet once. This is
expected, not a bug; just don't be surprised by the extra Glue DPU-hours on
that first run.

## Post-deploy verification (cheap test run)

1. Trigger a small run (HI-Small, no training) rather than HI-Large:
   ```powershell
   .\scripts\ingestion\ingest.ps1 -Subset HI-Small
   ```
2. Poll status: `.\scripts\ingestion\ingest.ps1 -Status <run_id>` or
   `python scripts/ingestion/inspect-ingestion.py --run-id <run_id> --output output/inspect/<run_id>`.
3. Confirm in the Step Functions execution: the third parallel branch runs
   `ReuseNetwork` → `Network` (Glue `is459-g3-ingest-network`), not `Corridors`.
4. Confirm `is459-g3-ingest-network`'s Glue job run `SUCCEEDED` in CloudWatch
   (`/aws-glue/is459-g3-ingest-network`).
5. Confirm the export stage passes — this is exactly what PR #6 fixed
   (`glue_compute.run_stage` now accepts the `network` channel instead of
   `athena`). If export still raises `Unexpected Glue input channels` or
   `Missing export verification input`, PR #6 didn't make it into what was
   deployed — stop and check.
6. Check `pipeline/runs/<run_id>/export/release/manifest.json`'s
   `verification` block has a `network` key (not `corridors`), and
   `evidence/result.json`'s `usage.glue` includes a `network` entry with
   nonzero DPU-hours.
7. Open the dashboard (`https://d1n61tlw9pcrog.cloudfront.net/`) and confirm
   the "Mule networks" section renders hubs/loops/rings for this run, and that
   "Incoming AML" still works unaffected.
8. Only after this passes, resume normal HI-Large / training runs.

## If something goes wrong mid-update

- `update-ingestion.ps1` doesn't apply anything until the allowlist check and
  your own review both pass — a rejection there is safe to walk away from.
- If `apply` itself fails partway, the message points at the retained private
  Terraform state (`%LOCALAPPDATA%\IS459-G3\terraform-ingestion`) for
  recovery; don't re-run blindly — inspect `terraform state list` /
  `terraform plan` again first.
- If a test run's `export` fails with the old Athena-shaped error, the Glue
  export job is loading a stale `bda_pipeline.zip` that did not pick up PR #6.
  Rebuild and redeploy `bda_pipeline.zip` (steps 3-6 again), and confirm the
  export job points at that artifact. The fix is not in the Lambda's
  `control.zip`.
