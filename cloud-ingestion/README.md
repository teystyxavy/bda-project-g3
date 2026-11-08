# AWS-side Kaggle and FX ingestion

The deployed Lambda `is459-g3-acquire` acquires and verifies inputs in AWS. The
Standard workflow `is459-g3-cloud-ingest` invokes it, then waits for the existing
batch pipeline to verify results and publish both dashboard views. Use the workflow
as the source-to-dashboard entry point; invoking Lambda alone prepares inputs.

```powershell
.\scripts\ingestion\ingest.ps1
.\scripts\ingestion\ingest.ps1 -NoWait
.\scripts\ingestion\ingest.ps1 -Status <run-id>
```

The launcher sends run options and needs AWS access; it does not need a local
dataset, FX file or Kaggle credential. Processing continues after submission if
the terminal closes. You can instead start the wrapper directly in the AWS console:

```json
{"run_id":"unique-cloud-run-id"}
```

Optional fields: `subset` (default `HI-Small`), `rows` for a smoke prefix, `dataset_version` for a version pin,
`refresh` to download again, `rerun` to bypass a successful processing cache, and
`train` to produce a private SageMaker candidate. Flags are JSON booleans. Training
requires the full subset and never automatically promotes a candidate. The default
is full HI-Small with the existing frozen model.

The selected transaction/account files enter the versioned S3 source cache.
Medium uses `pipeline/acquisition/HI-Medium/` so its version records and latest
pointer cannot overwrite Small records. Both files are pinned to the same resolved Kaggle version and hash-checked
on reuse. Historical FX is fetched each run and validated before the raw manifest
is committed. A successful unchanged manifest returns its prior release. Batch
jobs and acquisition are not automatically repeated after failure.

The image uses a pinned AWS Lambda Python 3.12 base. Terraform is in
`infra/cloud-ingestion/`; the deployment adds nine resources without changing
the existing batch stacks. The Lambda has a 15-minute timeout, 4 GB memory, 10 GB
temporary storage and concurrency one, with no VPC/NAT gateway or provisioned
worker. Secrets, if needed, are referenced by an existing Secrets Manager ARN.

[Deployment review](../planning/archive/reviews/cloud-ingestion-review.md) records the exact image,
permissions and deployment helper. [Live evidence](../planning/evidence/cloud-ingestion-deployment-result-20261006.json)
records acceptance status. [Runbook](../planning/runbooks/ingestion-runbook.md) documents
local compatibility, controls and failure recovery.

Read-only acquisition evidence can be retained with:

```powershell
python scripts/ingestion/inspect-cloud-acquisition.py --run-id <run-id> --output output/run-evidence
```

This checks wrapper history, the committed manifest, encrypted source metadata,
retained local source hashes when available, and historical FX coverage/provenance.
Batch and public release checks use the existing inspection/verification helpers.

## HI-Medium full runs and smoke checks

```bash
python scripts/ingestion/ingest.py --profile is459-g3-tools run --subset HI-Medium --dataset-version 8 --rows 1000
```

Deployed and verified on 7 November 2026: `medium-cloud-20261107-1000`
completed acquisition, all five Glue jobs and public publication. The public
pointer and snapshot SHA-256 were independently checked. Cold acquisition took
267.637 seconds.

The updated image accepts full HI-Medium runs or custom limits of 1–32,000,000 rows, without candidate training. Omit `rows` to select all 31,898,238 transactions in version 8. All
downloads, preparation and uploads happen in Lambda. Even a smoke run downloads
the complete source CSVs (about 3.18 GB), then prepares the selected prefix.
After verifying independent working copies, acquisition removes KaggleHub's duplicate download cache before preparing inputs, keeping the configured 10 GiB scratch disk available. The existing 15-minute timeout remains; a live cold
download must pass before cloud Medium acceptance can be claimed. The dashboard and launcher both support full HI-Medium acquisition in AWS.

Deploy acquisition image updates using
the existing shared `cloud-ingestion/terraform.tfstate` backend. The original
`deploy-cloud-ingestion.ps1` is an initial-creation helper and rejects updates;
do not use it to replace or recreate the existing stack. Review an update plan
that changes only the acquisition Lambda image and ephemeral storage.

[Medium deployment and smoke evidence](../planning/evidence/cloud-medium-result-20261107.json)
records the image, Terraform change, source/FX checks and live run status.

Full run (existing frozen model):

```bash
python scripts/ingestion/ingest.py --profile is459-g3-tools run --subset HI-Medium --dataset-version 8
```

The dashboard provides **HI-Medium → Full dataset**. Full runs use the medium resource profile for curation and scoring; baseline/export allow at least 60 minutes. Candidate training remains restricted to HI-Small. Acquisition has a 15-minute limit and refuses to begin uploads with less than two minutes remaining.

Full AWS acquisition was verified on 8 November 2026: all 31,898,238 version-8 rows, accounts, and FX across all 15 currencies were prepared and uploaded in 429.5 seconds. The check did not start batch processing or change public business results. Memory was raised to 4 GiB after the successful test. See [full Medium deployment evidence](../planning/evidence/full-medium-dashboard-deployment-20261108.json).
