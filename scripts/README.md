# Script guide

Run commands from the **repository root**, with the project installed as described
in the [main README](../README.md). Scripts are physically grouped by purpose.
Application logic lives in `src/bda_pipeline/`, deployed job entry points in
`jobs/` and the container directories, and exploration in `notebooks/`.

## Common commands

```powershell
# Sign in, then launch the deployed source-to-dashboard pipeline.
.\scripts\aws\configure-aws.ps1
.\scripts\ingestion\ingest.ps1 -Python python

# Download the default dataset for local work.
python scripts/ingestion/kaggle-download.py

# Inspect arguments without starting a pipeline run.
python scripts/ingestion/ingest.py --help

# Open the live dashboard using the existing Group 3 AWS identity.
python scripts/aws/dashboard-signin.py --profile is459-g3-tools

# Package the dashboard ingestion API locally; creates no AWS resources.
python scripts/aws/prepare-dashboard-api.py --output output/dashboard-control-package

# Test the ingestion browser logic with offline fixtures.
node --test tests/dashboard-controls.test.cjs

# Run the local suite; Spark checks use the Glue container separately.
python -m pytest tests --ignore=tests/spark -q
```

Launching ingestion can start paid jobs. See the
[ingestion runbook](../planning/runbooks/ingestion-runbook.md) for prerequisites,
source selection, caching, smoke runs and recovery. Generated artifacts belong
under ignored `output/`; downloaded data belongs under ignored `dataset/`.
For HI/LI, Small/Medium/Large, optional patterns and individual files, use
`ingestion/ingest.ps1 -DownloadOnly`; `-DryRun` previews the selection.
See [all download options and costs](../planning/runbooks/kaggle-download-options.md).

## Reading the code

Script docstrings and PowerShell help describe each command's pipeline role,
inputs, outputs and side effects. Shared modules under `src/bda_pipeline/` explain
the transformations and verification contracts; Glue jobs, SQL, Dockerfiles and
Terraform files describe their part of the same flow. Run Python commands from
the installed repository as above; `--help` shows arguments without running jobs.

For the newer dashboard paths:

- [build-exploration.py](dashboard/build-exploration.py) enriches verified local
  data and copies static assets; it makes no AWS calls.
- [prepare-dashboard-release.py](dashboard/prepare-dashboard-release.py) checks
  and packages the four assets locally.
- [publish-exploration.py](dashboard/publish-exploration.py) writes the prepared
  release to AWS and saves predecessor assets for recovery. Use only when
  publication is explicitly requested.
- [prepare-dashboard-status.py](aws/prepare-dashboard-status.py) packages the
  public read-only status handler; [prepare-dashboard-api.py](aws/prepare-dashboard-api.py)
  packages the separate authenticated control handler. Neither deploys resources.
- [prepare-dashboard-streaming.py](aws/prepare-dashboard-streaming.py) packages the
  publisher that turns committed streaming scores into the Incoming AML snapshot.
  It deploys nothing; see the [streaming guide](../streaming/README.md#incoming-aml-dashboard-view).

Comments explain code behavior, not deployment evidence. Use current runbooks
and recorded verification results for deployed status; changing documentation in
a packaged source file can change its artifact hash on the next build.

## Categories

| Directory | Purpose |
|---|---|
| [ingestion/](ingestion/) | Dataset/FX acquisition, pipeline launch and progress inspection |
| [local/](local/) | Local transformations, analysis and rule evaluation |
| [modeling/](modeling/) | Model comparison and SageMaker candidate training |
| [dashboard/](dashboard/) | Dashboard exports and local release packaging |
| [aws/](aws/) | Authentication, deployment preparation and guarded deployment helpers |
| [validation/](validation/) | Smoke fixtures, Glue parity tests and batch/release verification |
| [streaming/](streaming/) | Optional replay preparation, execution and evidence |
| [architecture/](architecture/) | Editable diagram generation and image exports |
| [benchmarks/](benchmarks/) | Local feature benchmarks and completed-run timing evidence |

## Complete command index

Paths below are relative to `scripts/`. Python argument details are available
through `--help`, except the diagram generator, which builds fixed diagrams.

| Category | Scripts |
|---|---|
| Ingestion | [ingest.ps1](ingestion/ingest.ps1), [ingest.py](ingestion/ingest.py), [kaggle-download.py](ingestion/kaggle-download.py), [fetch-fx-rates.py](ingestion/fetch-fx-rates.py), [inspect-ingestion.py](ingestion/inspect-ingestion.py), [inspect-cloud-acquisition.py](ingestion/inspect-cloud-acquisition.py), [check-ingestion.py](ingestion/check-ingestion.py) |
| Local analysis | [run-local-pipeline.py](local/run-local-pipeline.py), [run-local-analysis.py](local/run-local-analysis.py), [evaluate-local-risk.py](local/evaluate-local-risk.py) |
| Modelling | [compare-local-model.py](modeling/compare-local-model.py), [sagemaker-training.py](modeling/sagemaker-training.py) |
| Dashboard | [build-dashboard.py](dashboard/build-dashboard.py), [build-model-dashboard.py](dashboard/build-model-dashboard.py), [export-verified-batch.py](dashboard/export-verified-batch.py), [prepare-dashboard-release.py](dashboard/prepare-dashboard-release.py) |
| AWS setup and deployment | [configure-aws.ps1](aws/configure-aws.ps1), [plan-aws.ps1](aws/plan-aws.ps1), [deploy-ingestion.ps1](aws/deploy-ingestion.ps1), [deploy-cloud-ingestion.ps1](aws/deploy-cloud-ingestion.ps1), [update-ingestion.ps1](aws/update-ingestion.ps1), [prepare-ingestion.py](aws/prepare-ingestion.py), [prepare-cloud-ingestion.py](aws/prepare-cloud-ingestion.py), [prepare-cloud-scoring.py](aws/prepare-cloud-scoring.py), [prepare-orchestration.py](aws/prepare-orchestration.py), [prepare-sagemaker-container.py](aws/prepare-sagemaker-container.py), [bundle-scoring-wheels.py](aws/bundle-scoring-wheels.py), [refine-scoring-deployment-policy.py](aws/refine-scoring-deployment-policy.py) |
| Validation | [prepare-aws-smoke.py](validation/prepare-aws-smoke.py), [prepare-ingestion-smoke.py](validation/prepare-ingestion-smoke.py), [test-glue-local.ps1](validation/test-glue-local.ps1), [test-cloud-scoring-local.ps1](validation/test-cloud-scoring-local.ps1), [verify-aws-smoke.py](validation/verify-aws-smoke.py), [verify-aws-full.py](validation/verify-aws-full.py), [verify-cloud-scoring-smoke.py](validation/verify-cloud-scoring-smoke.py), [verify-cloud-scoring-full.py](validation/verify-cloud-scoring-full.py), [verify-ingestion-release.py](validation/verify-ingestion-release.py), [verify-orchestrated-corridors.py](validation/verify-orchestrated-corridors.py) |
| Streaming | [prepare-streaming-container.py](streaming/prepare-streaming-container.py), [prepare-streaming-demo.py](streaming/prepare-streaming-demo.py), [replay-streaming-demo.py](streaming/replay-streaming-demo.py), [verify-streaming-demo.py](streaming/verify-streaming-demo.py), [collect-streaming-evidence.py](streaming/collect-streaming-evidence.py), [collect-streaming-operations.py](streaming/collect-streaming-operations.py) |
| Architecture | [build-architecture-diagrams.py](architecture/build-architecture-diagrams.py), [export-architecture.ps1](architecture/export-architecture.ps1) |
| Benchmarks | [benchmark-glue-history.py](benchmarks/benchmark-glue-history.py), [benchmark-ingestion.py](benchmarks/benchmark-ingestion.py) |

Cloud preparation helpers create local artifacts; deployment helpers can write to
AWS. Verification helpers reconcile saved output or perform read-only cloud
inspection. The replay command defaults to a dry run. See the
[documentation index](../planning/README.md) for each milestone's prerequisites.

The previous flat `scripts/<name>` paths have moved to these categories. Runbooks
use the new locations; sanitized evidence retains paths recorded at execution
time. Add new scripts to the relevant category and this index.

## Shared Terraform state

From the repository root, `scripts/aws/init-terraform.ps1 -Stack <name>` connects
to an existing locked S3 state key. `scripts/aws/get-terraform-inputs.ps1 -Stack
<name>` also downloads deployed inputs/artifacts into private local storage and
checks their hashes. See [teammate setup](../infra/tfstate-bootstrap/README.md).
The artifact-retention helper now backs up `terraform state pull` from the
initialized shared backend rather than reading an obsolete local state file.
