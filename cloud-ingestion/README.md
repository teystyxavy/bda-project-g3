# AWS-side Kaggle and FX ingestion

The deployed Lambda `is459-g3-acquire` acquires and verifies inputs in AWS. The
Standard workflow `is459-g3-cloud-ingest` invokes it, then waits for the existing
batch pipeline to verify results and publish both dashboard views. Use the workflow
as the source-to-dashboard entry point; invoking Lambda alone prepares inputs.

```powershell
.\scripts\ingest.ps1
.\scripts\ingest.ps1 -NoWait
.\scripts\ingest.ps1 -Status <run-id>
```

The launcher sends run options and needs AWS access; it does not need a local
dataset, FX file or Kaggle credential. Processing continues after submission if
the terminal closes. You can instead start the wrapper directly in the AWS console:

```json
{"run_id":"unique-cloud-run-id"}
```

Optional fields: `rows` for a smoke prefix, `dataset_version` for a version pin,
`refresh` to download again, `rerun` to bypass a successful processing cache, and
`train` to produce a private SageMaker candidate. Flags are JSON booleans. Training
requires the full subset and never automatically promotes a candidate. The default
is full HI-Small with the existing frozen model.

Only the two HI-Small transaction/account files enter the versioned S3 source
cache. Both files are pinned to the same resolved Kaggle version and hash-checked
on reuse. Historical FX is fetched each run and validated before the raw manifest
is committed. A successful unchanged manifest returns its prior release. Batch
jobs and acquisition are not automatically repeated after failure.

The image uses a pinned AWS Lambda Python 3.12 base. Terraform is in
`infra/cloud-ingestion/`; the deployment adds nine resources without changing
the existing batch stacks. The Lambda has a 15-minute timeout, 3 GB memory, 4 GB
temporary storage and concurrency one, with no VPC/NAT gateway or provisioned
worker. Secrets, if needed, are referenced by an existing Secrets Manager ARN.

[Deployment review](../planning/archive/reviews/cloud-ingestion-review.md) records the exact image,
permissions and deployment helper. [Live evidence](../planning/evidence/cloud-ingestion-deployment-result-20261006.json)
records acceptance status. [Runbook](../planning/runbooks/ingestion-runbook.md) documents
local compatibility, controls and failure recovery.

Read-only acquisition evidence can be retained with:

```powershell
python scripts/inspect-cloud-acquisition.py --run-id <run-id> --output output/run-evidence
```

This checks wrapper history, the committed manifest, encrypted source metadata,
retained local source hashes when available, and historical FX coverage/provenance.
Batch and public release checks use the existing inspection/verification helpers.
