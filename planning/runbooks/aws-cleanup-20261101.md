# AWS cleanup — 1 November 2026

The user explicitly requested S3 cleanup and removal of unused AWS resources.
Scope was restricted to Group 3 in account `588738581042`, Singapore. Other
coursework buckets were excluded. No pipeline execution or training was started.

## Removed

- **170 S3 object versions, 4,539,490,364 bytes (4.54 GB)** from the private data
  bucket `is459-g3-588738581042-ap-southeast-1`. Deletion used exact key/version
  pairs, including any old versions, rather than adding storage-retaining delete
  markers.
- Four failed/aborted run prefixes: `20261031-153036-cdc7efaf`,
  `medium-full-20261030`, `glue-smoke-20261101-a`, and
  `glue-smoke-20261101-b`. Execution status and absence of release/stage-cache
  references were checked first. Small diagnostic JSON files were archived
  locally before deletion.
- Eleven obsolete immutable deployment prefixes. The current deployment
  `1435da83ea8e8e59490e4b3c13fd80bec1006e70a9d59aa7e075de5686c47754`
  and prior pre-migration deployment
  `d605126b9f46dda7ee74f38227c965178c2c6aaf2946c0a712bf087400a5f8cb`
  were retained. Retired bundles no longer provide exact historical runtime
  recovery; successful run outputs and verification records remain.
- Catalog table `is459_g3_ingest.run_glue_smoke_20261101_b`, which belonged to
  the aborted run.
- Three Terraform resources from `orchestration/`: the superseded
  `is459-g3-reviewed-batch` state machine and its dedicated IAM role/policy.
  Its execution history was archived locally first.
- Nine Terraform resources from `scoring/`: obsolete `is459-g3-score-frozen`,
  its two roles and inline policies, dedicated failure rule/target and two log
  groups. Current scoring remains `is459-g3-ingest-score`.

## Retained and checked

The four current Glue ingestion jobs, acquisition and ingestion workflows,
opt-in upload trigger, dashboard controls/status API, source acquisition cache,
all successful runs and stage/release cache receipts were retained. So were
verified Athena corridor results, historical training artifacts, frozen models,
the foundation's standalone ETL demonstration, the parked streaming demonstration,
monitoring, budget alerts, and all Terraform state/version history.

No workflows or Glue jobs were running and the pipeline lock was absent during
preflight. Current shared handoff artifact references were checked before removal.
All dashboard-origin asset hashes matched before/after deletion. Every selected
prefix was re-listed to confirm no current objects or older versions remained.
An independent full listing confirmed all 4,072 retained data-bucket objects
had unchanged sizes/ETags (167 current objects removed; 170 total versions).
All five retained Glue job definitions matched the pre-cleanup inventory.
The public dashboard and read-only status API both returned HTTP 200. These
checks did not start paid pipeline jobs.

The current pipeline is still acquisition → S3 → Glue curation → parallel Glue
baseline / frozen scoring / Athena → Glue export → verified dashboard publication.
Removing the predecessor stacks does not establish fresh full-population
acceptance of the Glue migration; its existing validation limits still apply.

## Terraform handoff

Both removed stacks were changed through saved, reviewed Terraform plans with
shared backend locking: **zero additions, zero updates, twelve removals**.
Both subsequent plans reported no changes. Their authoritative S3 states retain
their lineages, now at serial 2, and contain no managed resources.

Shared `handoff/current/inputs/` now sets `provision_orchestration=false` and
`provision_scoring=false`. Shared `handoff/current/artifacts.json` has matching
state serials. Conditional writes protected against overwriting concurrent
handoff changes. Historical committed recovery snapshots remain historical;
do not push them over the authoritative S3 states.

Detailed private inventory, exact deletion manifest, API deletion receipts,
diagnostic archives and verification output are under the gitignored local
`output/aws-cleanup-20261101/` directory. No private state or credentials were
added to Git. This cleanup reduces stored bytes; it does not claim a measured
monthly bill reduction.
