# Automated ingestion and SageMaker deployment review

Deployment update: the user approved the complete deployment/smoke/full-training
sequence, and the 33 additions and image are now deployed. The AWS smoke succeeded
and published its verified release; the full training run also passed and published. See [live deployment evidence](../../evidence/ingestion-deployment-result-20261006.json).
The following implementation/verification descriptions record the pre-deployment review.

At the time of review, implementation was complete locally. The new AWS resources, cloud processing, and
dashboard publication have **not** been executed. The existing verified dashboard,
batch runs, and disabled streaming consumer remain the live implementation.

## Three additions requested

1. Kaggle acquisition: version-pinned downloads, checksummed local reuse, duplicate
   Account header validation, and a reproducible input manifest.
2. Historical FX and source-to-dashboard automation: the ingestion command discovers
   transaction dates/currencies, retrieves daily/prior-day coverage, uploads raw
   inputs, and starts the independent AWS workflow. Both business views publish
   together after verification.
3. SageMaker ML: optional bounded CPU training with chronological splits, comparison
   against the rule baseline, validation-only selection, and private candidate
   artifacts. The active frozen model continues to serve the dashboard.

## Exact deployment scope

Account `588738581042`, Singapore `ap-southeast-1`.

| Stack | Reviewed Terraform changes |
|---|---|
| `infra/sagemaker` | 3 additions: immutable ECR repository, training execution role, scoped inline policy |
| `infra/ingestion` | 30 additions: 12 private S3 objects, 4 execution roles, 4 inline policies, 2 Glue jobs, 5 log groups, 1 Glue database, 1 Lambda function, 1 Standard Step Functions workflow |
| Combined | **33 additions, 0 updates, 0 deletions** |

The S3 objects are 11 allowlisted deployment artifacts plus `pipeline/deployment.json`.
Deployment also pushes one digest-verified CPU image to the new ECR repository.
It reuses the existing data/site buckets, CloudFront distribution, Athena workgroup
and failure SNS topic. Caller policy outputs are documentation; no policy attachment
to the signed-in user is performed automatically.

Runtime resources are separate from the Terraform count: each uncached ingestion
starts two Glue runs, two sequential SageMaker Processing jobs, one Athena query,
one run-specific projected catalog table, one workflow execution, and a CloudFront
invalidation. `-Train` additionally starts one SageMaker Training job. The optimized
SageMaker `.sync` integrations may create their standard EventBridge tracking rules.
No recurring ingestion schedule is installed; this version runs on command.

## Immutable deployment

- Local image: `is459-g3-ingestion:local-20261006-v5`.
- ECR manifest digest: `sha256:1005fb9fe3495126b635adad35aa6c59c0c36149fb059b9677fceefc21218c83`.
- Configuration SHA256: `2e4b7b2d5578948595be0a7c20616b2bb8efdb9a24aa41fb6daedbd02e86bcaf`.
- Local preparation: `output/ingestion-artifacts-20261006-v5/`.
- Artifact prefix: `pipeline/deployments/fd666160fbf5b583144bd05a0db51aa92f0414a8f20680e7a208322d14389148`.
- Private saved plans: `%LOCALAPPDATA%/IS459-G3/terraform-{sagemaker,ingestion}/deployment.tfplan`.

The deployment script verifies all prepared file hashes and the local image digest,
checks prerequisites/quotas, and rejects a fresh plan with mutations or a different
number of additions. Its default mode checks integrity only. `-Execute` is the
explicit deployment action after approval. Partial deployment requires inspection
of private state and a revised review before resuming; resources are never reset.

## Cost and runtime limits

Read-only AWS checks on 6 October confirmed processing quota 4 for `ml.m5.2xlarge`
and training quota 20 for `ml.m5.xlarge`. The workflow definition passed AWS validation
with no diagnostics. Current API prices in Singapore are USD0.576 per processing
instance-hour and USD0.288 per training instance-hour.

Each processing stage uses one CPU instance, 30GB volume and a 1,800-second runtime
limit. Training uses one CPU instance, 20GB volume and a 1,200-second limit, at most
120 boosting rounds and four threads. Glue jobs each use two G.1X workers, ten-minute
timeout, one concurrent run, and no job retries. Athena retains its 1GiB scan cutoff.
Compute tasks have no automatic retries; transient Lambda service failures retry
twice. Full workflow timeout is two hours.

At 30 billable minutes for each processing stage plus 20 for training, SageMaker
instance charges would be USD0.672. This is illustrative, **not a total-cost cap**:
startup, transfers, storage, Glue, Athena, logs, requests and CloudFront add charges.
The existing USD10 alert and USD250 course budget remain relevant; budgets alert
rather than stop jobs. No endpoint, notebook, Studio domain or always-running compute
is created.

## Verification completed

- Full local suite: 120 passed, 3 skipped. New tests cover acquisition/version reuse,
  FX coverage/staleness, SDK request shape, identity/hash changes, locks, publication,
  failed release preservation, frozen score parity, candidate provenance and metrics.
- Network-disabled Linux CPU containers processed a 1,000-row real-data fixture,
  verified original identities/FX/curation, built the rule baseline, reproduced model
  scores exactly, reconciled 177 daily bank/currency corridors, and produced a
  four-file dashboard release. Scores and Athena results in this local test were
  staged native/reference data; this is not an AWS Glue/Athena execution.
- `ingest.ps1 -PrepareOnly -Rows 1000` successfully reused local Kaggle inputs and
  fetched historical FX APIs. No upload, workflow execution, or publication occurred.
- JavaScript and PowerShell syntax checks passed; Terraform configuration validated.
- SageMaker training itself was previously tested in a network-disabled CPU container;
  integrated candidate training/export now passes the local regression test.
- Hash-only deployment-script mode passed.

Terraform mock-provider test results and exact hashes are recorded in the companion
validation JSON when final checks finish. Cloud IAM trust, managed `.sync` cancellation,
Glue staging, processing volume/runtime on the full dataset and CDN publication
still require an approved AWS smoke run. The source and local evidence do not claim
that these integrations have been demonstrated in AWS.

## Release and failure behaviour

One S3 conditional lock serializes paid workflows/publication. A failed run preserves
its outputs and lock; safe recovery requires the execution to be unsuccessful and
all its workers stopped. There is no timed lock expiry. Input hashes, dataset version,
deployment/model hashes, dates, population and training mode form the cache identity.
An identical completed run returns its immutable release URL; `-Rerun` uses fresh paths.

Each release goes to `dashboard/releases/<run>/`. The root shell follows a conditional,
SHA256-verified `dashboard/current.json` pointer. Without an initial pointer it loads
the existing `dashboard.json`; S3 may return 403 for an absent pointer under its scoped
origin policy. Failure before the pointer commit preserves the previous selected
release. Failures after commit can leave a verified new release selected while workflow
completion is being recovered; publication retries are idempotent and retain evidence.
Cached releases may be older than the root dashboard: use the returned release URL
to view the exact reused run.

All private candidate outputs stay in the data bucket. Training exports verify archive
safety, model hashes, source/image/job provenance and recomputed evaluation metrics.
Public queues exclude labels and private paths. Dashboard provenance shows run, dates,
row scope, FX/input hashes, refresh time and dataset version. Existing unversioned
downloads honestly display an unrecorded version; specify `-DatasetVersion` or `-Refresh`
to acquire a known current version. The reviewed model/date/schema contract supports
HI-Small only and rejects incompatible inputs.

## Approved deployment sequence to request

1. Deploy the 33 additions and push the reviewed CPU image.
2. Execute one 1,000-row AWS smoke run and verify the resulting public release.
   This temporarily selects a clearly labelled smoke dashboard; retain the previous
   release for rollback, then restore it or continue to the full release.
3. After smoke evidence passes, execute one complete HI-Small ingestion; include
   `-Train` only when that paid training run is explicitly approved.
4. Collect execution, job, query, reconciliation and publication evidence. Reuse the
   successful manifest to demonstrate caching without another paid run.

Deployment and paid-run approval is separate from implementing source, as required
by the workspace AGENTS.md. This review authorizes nothing by itself.

## Cleanup after presentation

Use reviewed Terraform plans for these two stacks. First stop any active workflow
and confirm workers stopped. Review removal of run-specific catalog tables, private
`pipeline/runs/` outputs, immutable deployment objects and releases separately. Retain
needed evidence/candidates before cleanup. The ECR repository prohibits force deletion,
so image removal needs explicit approval. Do not delete shared buckets, Athena/SNS,
CloudFront, the earlier workflow, or shared SageMaker tracking rules through this module.
