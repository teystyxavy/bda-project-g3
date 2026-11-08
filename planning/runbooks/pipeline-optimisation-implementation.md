# Full-pipeline optimisation

The existing AWS batch is now updated with parallel branches, bounded SageMaker
completion checks, verified stage reuse and streamlined export. AWS smoke/full
acceptance and observed timings are recorded in the accompanying deployment result.
This implements the full-rebuild improvements from phases A/B of the proposal.
Incremental transaction ingestion and the proposed streaming reconciliation design
remain separate future work; routine runs retain the complete existing audit.

The cache-bypassed 5,078,345-row rebuild `opt-full-20261006-aws1` completed in
20m55.083s versus the original 24m59.294s: 4m04.211s saved, or 16.3%. Its three input
files, Kaggle version and frozen model match the reference. Full native score
parity, corridor reconciliation, private/public file hashes and business-output
parity passed. This is one observed run per configuration, not an SLA or percentile.
The stage-cache acceptance with candidate training passed in 18m01.762s, with
both stages reused and zero new Glue jobs. The candidate passed compatibility
validation without promotion. An unchanged ingestion returned the existing verified
release in 33.850s and started no batch worker. See the
[final measured evidence](../evidence/pipeline-optimisation-result-20261006.json).

## Execution and assurance

```text
AWS Kaggle/FX acquisition + immutable run manifest
    → verified successful-release cache, when inputs/config match
    → conditional run lock
    → reusable curated objects OR Glue curation
    → encrypted success marker + run table
    → parallel:
        complete source/FX/causal-history verification + rule baseline
            → optional SageMaker candidate training
        reusable frozen scores OR Glue scoring
        Athena bank/currency corridor aggregates
    → all branches succeed
    → complete native score parity + independent corridor reconciliation
    → final dashboard release → conditional current pointer → release run lock
```

Baseline verification no longer delays scoring and corridor analysis. Candidate
training still requires the independently verified baseline. Export additionally
checks the baseline proof, native score census/fingerprints and every score's parity,
and both separate corridor grains. A failed branch cannot publish a dashboard.

SageMaker processing and training creation is submitted once, without automatic
paid-job retries. Each uses 15-second service-status checks, a 180-check bound and
the existing service runtime limit. Small read-only status requests may retry.
Output upload and service status propagation are included in observed transition
delay; processing compute-end timestamps alone do not mean output is committed.
The old baseline's final service modification occurred only 1.645 seconds before
the workflow continued; its 2m26s compute-to-next-state gap was mainly service/output
finalisation. The optimised run continued 4.259 seconds after baseline service
modification and 1.835 seconds after export modification. Parallelism produced the
main measured saving. Export compute took 238.871s versus 253.891s in the reference;
individual-worker timing can vary and does not establish causal savings alone.
Glue and Athena retain their synchronous integration and cancellation behavior.

The control function requests cancellation only for exact run-owned active workers
when a branch fails. A scoped EventBridge execution-status rule also handles
aborted/timed-out child workflows. Failed runs retain their lock, evidence and
partial outputs; recovery checks that all owned workers have stopped. EventBridge
delivery is asynchronous, with three delivery retries and a one-hour event-age
bound. Service runtime limits provide an independent compute bound.

## Stage reuse

Receipts live under `pipeline/stage-cache/{curated,scored}/<semantic-hash>.json`.
Only a fully verified, published run can register a receipt. They expire after 30
days; expiry causes a miss. Curation keys bind the transaction/account/FX file
hashes and sizes, row/date scope, Kaggle version, feature/schema version and
curation code/Glue runtime. Scoring additionally binds inference package/runtime
wheels, scoring job code and the frozen model manifest digest. Training and static
dashboard assets are deliberately outside curation keys.

Before reuse, the controller verifies the original manifest, baseline/publication
receipts and complete object inventory. Every copy checks size, encryption and
source ETag; S3 performs conditional server-side copies into the fresh run's
isolated prefix. `_SUCCESS` is copied last. Missing/corrupt/changed receipts or
objects fail closed before downstream processing. Cache reuse reduces paid work,
but baseline verification and full native parity still run for the new release.
`-Rerun` bypasses both release and stage caches for an actual rebuild benchmark.

## Worker optimisation

The intermediate baseline retains rule-scored Parquet, reconciled treasury data,
bounded summaries and provenance needed for training and final publication. It
omits static website assets and unused CSV presentation exports. The final export
downloads only its manifest/model, rule-scored reference, baseline summary,
persisted frozen scores, Athena output and optional candidate. It does not
download the raw archive, curated dataset or unrelated baseline exports again.

Evaluation selects the needed Parquet columns, sorts narrow key/label/score frames,
and copies feature/string columns only for the displayed top 100 explanations.
Complete scores remain available for exact PR-AUC. The existing final queue already
reused persisted scores; the independent full native-prediction pass is deliberately
retained as a verification gate. The baseline reference file is hashed once per
export instead of repeatedly when training is requested.

Glue retains two G.1X workers, causal windows, composite account keys, UTC,
`txn_date` partitions, no paid retries and its existing phase timing logs. No
worker increase or speculative shuffle/window rewrite is introduced without an
output-parity benchmark. The optimisation first removes proven serial waiting and
unnecessary transfer/materialisation work.

## Deployment and running

The update uses `scripts/aws/update-ingestion.ps1` with a freshly prepared directory
and digest-pinned image. Its preview checks the account, absent run lock, existing
private Terraform state, file/image hashes and an allowlist of resource changes.
`-Execute` pushes/verifies the ECR digest and applies that exact plan. It does not
start a batch. It refuses unreviewed changes and destructive replacement actions.

The update retained the predecessor's eleven immutable S3 artifacts using a
Terraform `removed` block with `destroy = false`; the new eleven artifacts are
managed under `deployment_artifact`. Three small resources implement terminal
cleanup: one execution-status rule, one target and one Lambda permission. No
always-on compute, endpoint, source polling schedule or streaming activation was
added. Final resource cleanup must also empty retained historical artifact prefixes.

The existing entry point stays the same:

```powershell
.\scripts\ingestion\ingest.ps1
.\scripts\ingestion\ingest.ps1 -Train
.\scripts\ingestion\ingest.ps1 -Rerun -NoWait
.\scripts\ingestion\ingest.ps1 -Status <run-id>
```

Use `scripts/benchmarks/benchmark-ingestion.py` for completed-run timing and stage-cache
evidence, alongside the existing acquisition, reconciliation and public-release
verification helpers. Record measured results rather than claiming the proposal's
12–16 minute target as an achieved SLA. Single-run comparisons are observations;
larger benchmarks should be considered against the course budget.
