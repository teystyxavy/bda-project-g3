# IS459 Group 3 — IBM AML data pipeline

An end-to-end AWS pipeline for the IBM Transactions for Anti-Money Laundering
[dataset](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml).

It answers two separate questions:

- **Compliance:** which transactions should investigators review first? A frozen
  XGBoost model produces a ranked queue with explanations and a rule baseline.
- **Treasury:** which bank and currency corridors dominate USD-equivalent volume,
  and how does concentration change? High volume does not imply suspiciousness.

[Open dashboard](https://d1n61tlw9pcrog.cloudfront.net/) ·
[Documentation index](planning/README.md) ·
[Demonstration runbook](planning/delivery/final-demo-runbook.md)

## Run the deployed pipeline

From this repository, use Python 3.11 or later and the project AWS profile:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[cloud]"
.\scripts\configure-aws.ps1
.\scripts\ingest.ps1 -Python python
```

The setup helper requires AWS CLI v2 and opens browser sign-in. The default launcher
uses the deployed AWS acquisition workflow; no local dataset or Kaggle credential
is needed. Kaggle and historical FX retrieval run in AWS, followed by Glue,
SageMaker verification, Athena corridor analysis and dashboard publication.

```powershell
# Submit and let AWS continue after the terminal closes.
.\scripts\ingest.ps1 -Python python -NoWait

# Inspect the run ID returned by submission.
.\scripts\ingest.ps1 -Python python -Status <run-id>

# Also train a private SageMaker candidate; no automatic model promotion.
.\scripts\ingest.ps1 -Python python -Train

# Force a complete rebuild, bypassing release and stage caches.
.\scripts\ingest.ps1 -Python python -Rerun
```

Unchanged verified inputs return their existing release. Compatible new runs can
reuse verified curated/scored stages. Every new publication retains complete
score parity, source/FX checks and corridor reconciliation. An uncached run starts
paid AWS jobs. See the [ingestion runbook](planning/runbooks/ingestion-runbook.md)
for source controls, smoke runs, failure recovery and deployment prerequisites.

## Verified results

The 6 October 2026 acceptance processed **5,078,345 transactions** from Kaggle
version 8, with **285 historical FX records across 15 currencies**.

| Observed run | Source-to-result time |
|---|---:|
| Earlier full rebuild | 24m59s |
| Optimised full rebuild | **20m55s — 16.3% faster** |
| Verified stage reuse plus SageMaker candidate training | **18m02s**, no new Glue jobs |
| Unchanged release reuse | **33.85s**, no batch processing |

These are individual measured runs, not service-level guarantees. All native
scores and treasury totals reconciled, and business rankings remained unchanged.
See the [optimisation implementation](planning/runbooks/pipeline-optimisation-implementation.md)
and [acceptance evidence](planning/evidence/pipeline-optimisation-result-20261006.json).

The optional Kinesis/Lambda/DynamoDB replay demonstration is separately verified;
its consumer remains disabled. Incremental ingestion and further streaming
optimisation are future work. Findings describe synthetic data, and model scores
are rankings rather than calibrated laundering probabilities.

## Repository map

| Directory | Contents |
|---|---|
| `src/bda_pipeline/` | Acquisition, transformations, features, scoring, verification and publication |
| `jobs/` | AWS Glue PySpark jobs and scoring dependencies |
| `infra/` | Separate Terraform stacks and workflow definitions |
| `cloud-ingestion/` | AWS acquisition container |
| `sagemaker/` | Training and processing container entry points |
| `streaming/` | Optional replay consumer container |
| `dashboard/` | Static HTML, CSS and JavaScript assets |
| `scripts/` | Stable run, prepare, deploy and verification commands |
| `sql/` | Athena analysis and reconciliation queries |
| `tests/` | Python unit/integration checks and separate Spark parity tests |
| `planning/` | Current design, runbooks, diagrams, evidence and archived milestones |
| `dataset/`, `output/` | Ignored local data, artifacts, tools and run outputs |

Use the [script guide](scripts/README.md) to find commands and the
[infrastructure guide](infra/README.md) to find stacks. Script locations are stable.

## Develop and validate locally

```powershell
python -m pip install -e ".[dev,ml,ingest]"
python -m pytest tests --ignore=tests/spark -q
```

The latest non-Spark suite passed **136 tests**, with one skipped. Spark parity
runs separately in the Glue container through `scripts/test-glue-local.ps1` and
`scripts/test-cloud-scoring-local.ps1`; see the
[batch runbook](planning/runbooks/aws-batch-runbook.md) for fixtures and prerequisites.
Terraform mock tests live under each stack's `tests/` directory. Historical local
analysis commands are retained in the
[development history](planning/archive/reviews/local-development-history.md).

## Data, credentials and cost

The default subset is `HI-Small` (about 510 MB); the full dataset is about 39 GB.
Datasets, generated model/dashboard artifacts, environment files, keys, credentials,
Terraform variables and state stay outside Git. AWS settings/state are kept under
`%LOCALAPPDATA%/IS459-G3`; Kaggle credentials belong in the user's Kaggle profile
or the configured AWS secret. Provider lock files and sanitized evidence are committed.

The course budget is **USD 250**. The deployed monthly alert is **USD 10**; it is
an alert rather than a spending cap. Jobs use bounded runtimes and no automatic
paid-job resubmission. Hosting/storage and the provisioned streaming shard can
continue to incur charges. See [deployment status](planning/aws-deployment-status.md)
and the runbooks for resource cleanup after the final presentation.
