# HI-Medium batch run

> **Current, 9 November 2026.** Full HI-Medium runs work from the dashboard's Ingestion
> tab since 8 Nov ([evidence](../evidence/full-medium-dashboard-deployment-20261108.json)).
> HI-Medium was also scored with the delta1 model on 7 Nov as isolated release
> `medium-delta1-20261107-183216` ([run](../evidence/hi-medium-delta1-run-20261107.json)).
> The live dashboard now shows HI-Large; see the [HI-Large runbook](hi-large-delta1.md).

**1 November 2026 deployment update:** baseline and export now
run as separate Glue G.2X jobs; SageMaker is reserved for optional Small training.
See [migration validation and deployment](glue-stage-migration.md). A 1,000-row HI-Small smoke passed; previous Medium AWS/public-snapshot evidence
does not establish full-population acceptance of this new Glue runtime.


**7 November 2026:** the acquisition Lambda now accepts cloud Medium smoke
prefixes of 1–1,000 rows, with isolated versioned source caching and 10 GiB
scratch storage. The 1,000-row cloud smoke passed through public publication,
with all five Glue jobs successful and the public snapshot hash verified.
See [deployment and live evidence](../evidence/cloud-medium-result-20261107.json).

Implementation branch: `feature/larger-dataset`.

## Source and scope

Kaggle version 8 was downloaded and profiled locally on 29 October 2026:

| Property | Observed value |
|---|---:|
| Transactions | 31,898,238 |
| Laundering labels | 35,230 |
| Date range | 1–28 September 2022 |
| Transaction CSV bytes | 3,031,783,420 |
| Account CSV bytes | 145,008,642 |
| Payment currencies / formats | 15 / 7 |
| FX records, including the preceding day | 435 |

The files remain in ignored `dataset/`. Source SHA-256 hashes are retained in
`HI-Medium_download.json`; run manifests independently hash the prepared inputs.
Both account joins use the composite bank/account key. Transaction IDs start
with `HI-Medium:` and cannot be mixed with `HI-Small:`. The source remains one
immutable CSV, preserving deterministic physical row indexes.

The first pass uses the existing frozen HI-Small model and a rule baseline.
It checks every score and reconciles the complete treasury population. It does
not retrain or promote a model. Metric changes describe synthetic populations;
possible overlap with the original model population prevents an independent
generalisation claim. Existing chronological training/validation/test windows
are retained; the tail extends through the last observed transaction date.

## Run after deployment readiness is established

From the repository root, using the configured project environment:

```powershell
# Preview only: no network, writes, uploads or AWS jobs.
.\scripts\ingestion\ingest.ps1 -Subset HI-Medium -LocalAcquisition -DryRun

# Download/reuse the version-pinned source without starting AWS jobs.
.\scripts\ingestion\ingest.ps1 -DownloadOnly -Subset HI-Medium -DatasetVersion 8

# Cloud smoke after the updated acquisition deployment (no local dataset required).
.\scripts\ingestion\ingest.ps1 -Subset HI-Medium -DatasetVersion 8 -Rows 1000

# Local-acquisition AWS smoke, over the first 1,000 source rows.
.\scripts\ingestion\ingest.ps1 -Subset HI-Medium -LocalAcquisition -DatasetVersion 8 -Rows 1000

# Full batch: no -Rows limit; acquire locally, upload, process and publish in AWS.
.\scripts\ingestion\ingest.ps1 -Subset HI-Medium -LocalAcquisition -DatasetVersion 8
```

Full HI-Medium requires local acquisition. The updated cloud acquisition source
supports bounded Medium smoke runs (1–1,000 rows) after its new image and
10 GiB scratch-storage configuration are deployed; see
[cloud ingestion](../../cloud-ingestion/README.md#medium-cloud-smoke-requires-updated-acquisition-deployment). Dashboard web run controls also remain HI-Small;
the published results identify the subset from the manifest. `-Train` is rejected
for Medium until a separate training and validation design is implemented.
Do not use `-Rerun` unless a complete rebuild is needed: unchanged verified inputs
can reuse results. Small and Medium stage caches have distinct identities.

## Resource and verification bounds

| Stage | HI-Small | HI-Medium |
|---|---|---|
| Glue curate / score | 2 G.1X workers, 10 minutes each | 4 G.1X workers, 30 minutes each |
| Curation shuffle partitions | 8 | 32 |
| Glue baseline / export | 2 G.2X workers, 30 minutes each | 2 G.2X workers, 60 minutes each |
| Manifest maximum rows | 5,100,000 | 35,000,000 |
| Maximum bytes per prepared input | Below 2 billion | Below 4 billion |

The workflow passes bounded worker counts/timeouts per run. Defaults on the Glue
job definitions remain the Small settings. There are no paid-job retries. The
workflow deadline is four hours. Glue tasks use synchronous completion; only
optional SageMaker training uses bounded 15-second polling.
The existing Athena 1 GiB scan guard remains in force; measure actual scans and
stop for review if it is exceeded rather than silently lifting it.

Source and score checks read 100,000-row batches. Dashboard AML projections keep
compact exact ranking inputs and bounded full-feature queues, rather than whole
evaluation periods of transaction columns. Corridor partials are combined
periodically. Exact metric arrays and distinct corridor groups still grow with
population/cardinality; these changes do not claim unbounded-memory scalability.
All score parity, source census, FX and corridor checks must pass before release.

## Cost planning

Singapore public on-demand rates checked on 29 October 2026:

- Glue 5.0: USD 0.44/DPU-hour. Two jobs using four DPUs for 30 minutes each:
  USD 1.76.
- Migrated Glue baseline/export: two jobs using four DPUs for 60 minutes each:
  USD 3.52 at that historical Glue rate.
- The revised runtime allowances sum to USD 5.28 using the 29 October rate.
  This is a planning illustration, not measured cost or an invoice cap.
  Provisioning/transfer billing, Athena, S3, requests, logs and orchestration
  may add charges, and failed/repeated runs also cost money.

Rates: [Glue regional price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSGlue/current/ap-southeast-1/index.json),
[SageMaker regional price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonSageMaker/current/ap-southeast-1/index.json).

Check current account/project spending and quota readiness before the AWS run.
The course ceiling is USD 250; the recorded USD 10 monthly alert is not a hard
spending cap. Do not change it implicitly to accommodate Medium.

## Acceptance status

The public release `medium-full-20261030-v2` now contains all **31,898,238**
HI-Medium rows and 28 treasury days (1–28 September 2022). The CloudFront pointer
and downloaded snapshot SHA-256 match. Both rule and frozen-model views are
present. See [the verification record](../evidence/hi-medium-result-20261030.json).
Final Step Functions status, private score/corridor evidence, cache-reuse and
actual compute timing remain pending renewed AWS sign-in; public publication
alone does not establish those remaining checks.

The first execution was rejected before any Glue job started because Glue
requires WorkerType alongside an overridden NumberOfWorkers; both fields are
now explicit. The retry reused the verified uploaded inputs within S3.
The 1,000-row **local** pass ran the real Medium source through the official Glue
5 image, then passed independent raw/FX/identity/behaviour checks. A synthetic
Medium source-to-release integration test also checks native score parity,
corridor reconciliation, publication, and rejection of corrupted scores.
Local regression: 228 Python tests passed (three optional tests skipped),
12 tests passed in the official Glue runtime, and 15 dashboard tests passed.
These local checks alone do not establish full Medium AWS acceptance.

Local artifacts are under `output/hi-medium-20261029/`. Before applying, obtain
the authoritative ingestion state from its operator/shared backend; do not
restore the historical ingestion backups as current state. Use the existing
artifact preparation and allowlisted update helpers with the new worker image.
No direct AWS resource update is a substitute for the Terraform plan.

### Deployment retention and ECR fallback

The updater now backs up private Terraform state and forgets only predecessor
immutable deployment uploads before planning. Their S3 objects are retained;
new deployment objects are created under the new content hash. This avoids a
replacement plan deleting artifacts referenced by earlier releases. Even a
preview can therefore update local artifact ownership in state; its backup is
kept next to the private state file. It changes no AWS resources without `-Execute`.

If Docker's credential store fails, an OCI archive can be uploaded through
`scripts/aws/push-ecr-archive.py`, using the existing AWS profile without creating
a registry password store. The helper validates the reviewed root digest,
checks uploaded blobs and verifies the remote image. Use
`update-ingestion.ps1 -ImageAlreadyPublished -Execute` afterwards; this still
checks the local/prepared/remote image digests, artifacts, account, idle pipeline
and allowlisted Terraform plan. No credential-store changes are required.
