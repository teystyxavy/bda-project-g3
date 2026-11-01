# Glue baseline and dashboard export migration

Deployed 1 November 2026. **Fresh 1,000-row HI-Small AWS smoke passed end to end.**
Full-population acceptance of the new Glue baseline/export runtime remains pending.

## Resulting pipeline

Lambda acquisition -> S3 raw -> Glue curation -> parallel branches:

- Glue baseline: independently verify the curated rows against raw transactions,
  accounts, historical FX and causal histories; calculate rule scores and metrics.
  Optional SageMaker candidate training follows this verified baseline.
- Glue frozen-model scoring for the investigation queue.
- Athena corridor analysis for treasury.

After all requested branches finish, Glue export verifies every model score,
reconciles corridors, reconstructs explanations, checks any training candidate,
and builds the four dashboard release assets. The publication Lambda verifies
hashes, privacy and provenance before committing the CloudFront dashboard pointer.
SageMaker is used only for optional model training; candidates are never promoted
automatically. The two business questions and chronological evaluation are unchanged.

## Jobs and contracts

| Job | Runtime | Bounds |
|---|---|---|
| `is459-g3-ingest-curate` | Existing Glue 5 Spark | Existing subset bounds |
| `is459-g3-ingest-score` | Existing Glue 5 Spark | Existing subset bounds |
| `is459-g3-ingest-baseline` | Glue 5; Python reference calculations on driver | 2 G.2X workers; 30 min Small / 60 min Medium; no retries |
| `is459-g3-ingest-export` | Glue 5; Python reference calculations on driver | Same as baseline |

The independent pandas/NumPy/XGBoost reference calculations are reused, rather
than rewritten to share Spark curation logic. These two jobs use the driver;
adding workers does not distribute their calculations. The G.2X driver supplies
the memory class used by the preceding verification design. Full-population
memory, scratch disk, duration and cost still need fresh cloud acceptance.
There is no claim that Glue makes these stages cheaper or faster.

The shared offline wheel bundle pins Python 3.11 dependencies from
`jobs/scoring-requirements.txt`. No runtime package downloads are needed.
Unlike the old network-isolated SageMaker jobs, the Glue worker uses its IAM
role to stage and upload S3 files. It receives no dashboard publishing rights.

All run data remains below
`s3://is459-g3-588738581042-ap-southeast-1/pipeline/runs/<run_id>/`:

- Baseline inputs: `raw/manifest.json`, `raw/`, `curated/`, pinned model deployment.
- Baseline output: `baseline/proof.json`, `baseline/baseline/bq1_scored.parquet`,
  `baseline/manifest/dashboard.json` and `baseline/manifest/input.json`.
- Export inputs: run manifest, baseline Parquet/manifest, `scored/`, pinned model,
  validated Athena CSV, and candidate archive only when training was requested.
- Export output: `export/release/manifest.json`, four allowlisted files under
  `export/release/site/`, and existing verification evidence.

`jobs/pipeline_stage.py` invokes `bda_pipeline.glue_compute`. It validates the
run/deployment and exact input-channel mapping, stages S3 prefixes with pagination
and safe local paths, and refuses any existing output prefix. It checks available
scratch space before each download. Calculations finish before uploads begin;
an encrypted `_SUCCESS` marker is uploaded last. Partial uploads cannot be used
as a completed stage. The controller checks the exact Glue job-run ID, successful
status, run/stage/deployment/output arguments and marker before training or publication.

Failure/abort cleanup and manual recovery inspect all four Glue jobs plus optional
SageMaker training. Other runs are never cancelled, and active workers prevent
unlocking. Compute jobs have no automatic retries. Use a fresh run ID after failure.

## Prepare and review deployment

The user explicitly authorized deployment and a small smoke test on 1 November.
The steps below document preparation and review for subsequent changes.
Follow the existing ingestion setup for AWS sign-in, shared Terraform state,
model artifacts and the offline wheel bundle. Do not reuse a pre-migration prepared
directory: new preparation includes the stage worker, dashboard shell and updated
controller/package, and produces a new deployment hash.

1. Build a training-only image using `scripts/aws/prepare-sagemaker-container.py`
   and `sagemaker/Dockerfile` as described in the SageMaker runbook. Pin its digest.
   The removed Processing entry point and dashboard assets are not included.
2. Prepare a fresh artifact directory with the implemented command:

   ```powershell
   python scripts/aws/prepare-ingestion.py --output output/glue-stage-artifacts --models <verified-model-directory> --wheels <runtime.gluewheels.zip> --image-uri <digest-pinned-training-image>
   ```

3. Preview the existing-stack update using
   `scripts/aws/update-ingestion.ps1 -PreparedDirectory <directory> -Image <image>`.
   Review the plan before any separately authorized `-Execute` invocation.
   The helper permits creation of the two Glue jobs and their four log groups,
   updates to Glue trust/permissions, workflow permissions, controller and
   artifacts, and deletion **only** of the obsolete Processing IAM role/policy.
   It still rejects other deletions/replacements and refuses updates while locked.
   Historical immutable artifacts and all existing run outputs are retained.
4. Repackage/review the dashboard status and control stacks using their existing
   instructions, because their bundled service labels also changed. Dashboard
   shell assets are included in the ingestion preparation for publication.
5. After an explicitly approved deployment, verify the deployed workflow has only
   training SageMaker API tasks and no Processing permissions. Verify Glue capacity
   and training quota if training is requested. There is no recurring schedule.
6. After separate paid-run approval, run the documented 1,000-row smoke with a fresh
   run ID, inspect both Glue stage markers and proofs, then test the full dataset.
   Check no SageMaker Processing jobs started, optional training still works,
   native scores/corridors reconcile, and publication/status labels are correct.
   Validate HI-Medium separately before claiming its new Glue runtime accepted.

The two G.2X jobs each consume four DPUs while running. Estimate compute as
`4 * runtime_hours * current_regional_DPU_hour_price` per stage, plus other services.
They can overlap frozen scoring; account DPU capacity must cover that concurrency.
The USD 250 course budget and USD 10 monthly notification remain unchanged.

## Local validation

Run the ordinary non-Spark suite from the repository root:

```powershell
python -m pytest tests --ignore=tests/spark -q
```

Run `scripts/validation/test-glue-stages-local.ps1` with an existing Linux wheel
cache to check the same suite offline in the pinned Glue 5 container. No AWS
credentials or networking are used. The tests include real baseline/export
calculations for both subsets, candidate verification, tamper rejection, input
staging, incomplete-upload rejection, stage identity and worker cancellation.

Regenerate ASL with `python infra/ingestion/build-workflow.py`.
Run Terraform `validate` and `test -var-file=<prepared terraform.tfvars.json>`
under `infra/ingestion` with an isolated local provider data directory and the
backend disabled. Tests use a mocked AWS provider, never real resources.

Editable architecture sources and the dashboard service map identify Glue for
baseline/export and SageMaker only for training. Historical exported PNGs and
AWS evidence must not be cited as acceptance of this migration.

Local validation on 1 November: **244 passed, 3 skipped** in the offline Glue 5
non-Spark suite; Terraform validation and both mocked ingestion tests passed.
The three updated diagram exports were rendered and visually reviewed. See the
[validation record](../evidence/glue-stage-migration-validation-20261101.json).

## Verified AWS acceptance, 1 November 2026

Run `glue-smoke-20261101-c` used Kaggle version 8 and 1,000 HI-Small rows,
with stage/release caches bypassed and candidate training disabled. Both workflows
succeeded. Glue curation, frozen scoring, baseline and export all succeeded;
reported compute durations were 104, 68, 116 and 69 seconds respectively. The
batch workflow took 440.424 seconds including orchestration/startup waits.

Independent curation proof passed for every row. All 1,000 scores matched native
predictions exactly (maximum absolute difference zero). All 177 treasury corridor
rows reconciled within the documented USD/relative tolerances. Private/public
release file hashes, root shell, cache receipt and lock release passed. No
SageMaker jobs ran and no model was promoted. All three changed Terraform stacks
had no drift. Private shared teammate inputs were refreshed to the final revision.

The preceding 5,078,345-row dashboard snapshot was restored with a conditional
pointer write and its public SHA-256 rechecked; the new Glue service-map shell
was retained. The smoke release remains available under its immutable run URL.

The smoke process exposed curation signature/date-argument gaps, frozen-schema
compatibility and AWS runtime response/SDK differences; these are corrected and
covered by regression tests. Earlier failed/aborted attempts and their outputs
were preserved. The final full offline Glue suite passed 262 tests (3 skipped),
including Spark; 31 final runtime-adapter regressions and 18 dashboard JavaScript
tests passed separately. No full-data or training run was started.

See [deployment and smoke evidence](../evidence/glue-stage-deployment-result-20261101.json).
