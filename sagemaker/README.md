# SageMaker AML training

Implementation status: the SageMaker foundation is deployed. Baseline and export
Processing jobs passed both AWS runs. Full training job
`is459-g3-train-full-20261006-aws1` completed with 159 billable seconds. The verified
depth-5 candidate passed its validation gate: precision@100 46% versus baseline 2%;
test precision@100 44% versus baseline 0%. Candidate artifacts remain private. This is an on-demand **candidate training** path.
The verified dashboard, Glue scorer and streaming image keep their existing model.

The trainer reuses `bda_pipeline.model.compare_models`: September 1-6 training,
September 7-8 validation, September 9-10 main test, and a separately reported sparse
September 11-18 tail. Two depth candidates are selected on validation average
precision, then precision@100, then name. The choice is saved before test labels
are loaded. Training-only category vocabularies and class weights are retained.
Scores are ranking outputs, not calibrated probabilities.

## Inputs and outputs

Input is the **verified full baseline export**, not raw CSV or model predictions:
`bq1_scored.parquet` contains causal engineered features and rule scores, paired
with its `dashboard/dashboard.json` source-hash and evaluation manifest. The
existing export is `output/cloud-insights-20261005/`. The implemented
[ingestion workflow](../planning/runbooks/ingestion-runbook.md) produces the same baseline
contract in a SageMaker Processing stage before invoking this trainer with `-Train`.
That workflow is deployed and passed the AWS smoke.

SageMaker stages two File-mode channels into `/opt/ml/input/data/`: `baseline/`
and `manifest/`. The container verifies hashes and counts before training, needs
no network access, and packages compatible `.ubj`, `features.json`, `selection.json`,
`comparison.json` and explained queues into `model.tar.gz`. A comparison report
is also written to `/opt/ml/output/data/`. Artifacts stay in the private S3 bucket
under `sagemaker/outputs/<job-name>/output/`.

A failed validation-baseline gate still returns a successful **training job** and
an ineligible candidate report. Job completion must never mean model promotion.
`SavedRanker` verifies artifact hashes when collected. Promotion also requires
runtime inference parity with Glue/Lambda and a reviewed dashboard release.

## Local preparation

From the project root, using the existing ML environment:

```powershell
./output/ml-venv/Scripts/python.exe -m pip install -e ".[ml,cloud,dev]"
./output/ml-venv/Scripts/python.exe -m pytest tests/test_sagemaker_training.py tests/test_model.py -q
./output/ml-venv/Scripts/python.exe scripts/aws/prepare-sagemaker-container.py --output output/sagemaker-build
docker build --platform linux/amd64 --provenance=false --sbom=false -t is459-g3-training:reviewed output/sagemaker-build
```

Build contexts contain source only. Python 3.12 is required by the pinned
XGBoost CPU 3.4.1 wheel. The base image is pinned by digest; numerical dependencies
are pinned to the versions used by the existing trainer. Record the final ECR
digest and review its scan before submitting cloud training.

## AWS provisioning and image push

Deployment and paid training require explicit approval under the workspace
`AGENTS.md`. Infrastructure is isolated in `infra/sagemaker/` and creates exactly
three resources: an immutable ECR repository, a SageMaker execution role, and its
scoped inline policy. No endpoint, notebook, Studio domain or scheduled job is
created. Existing AWS stacks are not changed.

Keep Terraform data, plans and state outside OneDrive, following existing project
practice. After approval, use these commands from the project root:

```powershell
$env:AWS_PROFILE = 'is459-g3-tools'
$env:AWS_REGION = 'ap-southeast-1'
$env:AWS_DEFAULT_REGION = 'ap-southeast-1'
$env:AWS_SDK_UA_APP_ID = 'AWSSkill-SageMaker'
$smPrivate = Join-Path $env:LOCALAPPDATA 'IS459-G3/terraform-sagemaker'
New-Item -ItemType Directory -Force -Path $smPrivate | Out-Null
$env:TF_DATA_DIR = $smPrivate
./output/tools/terraform/terraform.exe -chdir=infra/sagemaker init "-backend-config=path=$smPrivate/terraform.tfstate"
./output/tools/terraform/terraform.exe -chdir=infra/sagemaker plan "-out=$smPrivate/deployment.tfplan"
# Review the saved plan before this deployment step.
./output/tools/terraform/terraform.exe -chdir=infra/sagemaker apply "$smPrivate/deployment.tfplan"

aws ecr get-login-password --region ap-southeast-1 | docker login --username AWS --password-stdin 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com
docker tag is459-g3-training:reviewed 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training:reviewed-v1
docker push 588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training:reviewed-v1
```

The launcher needs the actions in Terraform's `launcher_policy_json` output;
that output does not attach permissions. The image publisher separately needs
ECR authentication and push permissions for this repository. No broad managed
SageMaker policy is required by the training role. Trust is limited to Group 3
training job ARNs in Singapore. Reuse the current authenticated AWS profile;
credentials must not appear in plans, inputs or reports.

## Prepare, start, inspect and collect

After recording the reviewed image digest, set `$smImage` to its complete
`588738581042.dkr.ecr.ap-southeast-1.amazonaws.com/is459-g3-training@sha256:...`
URI. A tag such as `latest` is rejected. Use a fresh job name and directory:

```powershell
./output/ml-venv/Scripts/python.exe scripts/modeling/sagemaker-training.py prepare --scored output/cloud-insights-20261005/bq1_scored.parquet --snapshot output/cloud-insights-20261005/dashboard/dashboard.json --output output/sagemaker-prepared-v1 --job-name is459-g3-train-full-v1 --image-uri $smImage
# The command above is local only. This command starts one paid job:
./output/ml-venv/Scripts/python.exe scripts/modeling/sagemaker-training.py start --prepared output/sagemaker-prepared-v1 --execute
./output/ml-venv/Scripts/python.exe scripts/modeling/sagemaker-training.py status --prepared output/sagemaker-prepared-v1
./output/ml-venv/Scripts/python.exe scripts/modeling/sagemaker-training.py collect --prepared output/sagemaker-prepared-v1 --output output/sagemaker-model-v1
```

Submission claims its input prefix with a conditional S3 write before uploads;
an existing claim stops the operation. Failed uploads preserve the claim and
require a fresh name. A lost training acknowledgement requires inspecting the
same job name, not submitting under another name. Submission has no SDK retry.
Collection requires `Completed`, checks the requested image, inputs, resource
limits and isolation, safely extracts only bounded flat files, and verifies
model/source provenance and native model reload. It never publishes a dashboard.

Default capacity is **one `ml.m5.xlarge`, 20 GB volume, 120 boosting rounds maximum,
20-minute training runtime limit**, no Spot retry and no automatic compute retry.
The instance is managed for the training job; no continuously running endpoint
is provisioned. The time limit is not a dollar budget or guaranteed total elapsed
time. Check Singapore pricing and the account's training quota before deployment;
use a short cloud smoke job before the full population. Storage, ECR and logs have
separate ongoing costs. Log-group retention is not changed because the SageMaker
TrainingJobs group can be shared with other jobs.

## Connection to the implemented ingest workflow

Ordinary ingestion should use the current approved model. An explicit training
mode can invoke this request through Step Functions `sagemaker:createTrainingJob.sync`
after creating the verified baseline export. It must collect/evaluate the candidate
before publication. The separate `infra/ingestion` module now implements that branch
and the one-command workflow. Local integration/container tests and the full AWS
training/publication run passed. See [the combined implementation review](../planning/archive/reviews/ingestion-implementation-review-20261006.md).
The earlier reviewed scoring/treasury state machine remains unchanged.

## Official references

- [Custom training image contract](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html)
- [CreateTrainingJob API and runtime controls](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html)
- [Execution-role confused deputy protection](https://docs.aws.amazon.com/sagemaker/latest/dg/security-confused-deputy-prevention.html)
- [Training execution-role permissions](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-roles.html)
- [SageMaker pricing](https://aws.amazon.com/sagemaker-ai/pricing/)

After the final presentation, include this role, repository, model/input objects
and training logs in a separately approved cleanup. There is no endpoint to delete.
