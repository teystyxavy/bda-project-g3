# HI-Large export fallback, 8 November 2026

## Speed improvements and handover, 13:07 SGT

The speed-aware controller in `output/speed-hardened-export-20261108/`
supersedes `output/hardened-export-20261108/`. The older controller was stopped
while still waiting; neither cloud run was interrupted. Use the new directory's
`status.json`, `controller.pid`, `controller.log`, and `controller.err`.

The new immutable package adds four bounded concurrent input downloads with
in-flight scratch-disk reservations, downloaded-size checks and progress logs.
Score verification uses up to sixteen processes, capped by available CPU and
population size, instead of eight. Fingerprint normalization uses vectorized
datetime and string checks while preserving the exact canonical SHA-256 bytes.
Dashboard populations are partitioned once per dimension instead of filtering
each batch separately for every currency, format and bank option. Worker type
and worker count remain unchanged; full-population checks and exact metrics
are retained.

A local 30,000-row fingerprint microbenchmark measured about 1.23x throughput
against the pinned original implementation, with identical fingerprints.
The synthetic payment-lookup benchmark measured about 1.66x steady-state
throughput, but initial index construction made a four-batch cold run slower;
this is not evidence for a particular full HI-Large AWS completion time.
The benchmark JSON files are under `output/speed-*-benchmark-20261108.json`.

The handover still requires the unfixed producer's complete result checkpoint.
It now retains an active corrected run once its logs show at least 75% of the
payment scan, completed network restoration, or a later export phase. Once
that decision is made it remains in effect until success/failure, even if old
progress messages fall out of the recent log window. A successful corrected
run prevents replacement; a failure can still recover from the source checkpoint.
Only a less-advanced corrected consumer is eligible for the previously
documented checkpoint-preserving replacement. The producer is never stopped.

The actual speed-package modules passed 105 tests. Coverage includes concurrent
download bounds and size rejection, exact fingerprints across datetime units
and timezones, integrity failures, checkpoint reuse, and avoiding unnecessary
handover of an advanced corrected run. JavaScript syntax and whitespace checks
passed. Both live runs retain their already-loaded code; these speed changes
apply only if the controlled replacement is needed. Keep the computer awake
and connected for the controller. Full cloud speed/completion remains unverified.

## Hardened handover controller, 12:56 SGT

At the user's request to prevent failures from the payment scan onward,
`output/hardened-export-20261108/` contains a tested, immutable hardened
deployment and a hidden handover controller. Its PID is in `controller.pid`;
inspect `status.json`, `controller.log`, and `controller.err`. Keep the computer
awake and connected. This controller supersedes the earlier waiting watchers;
do not restart them.

The controller waits for the unfixed run's complete network-result checkpoint,
checking its exact source-manifest/code identity, complete table set, checksum
fields and nonempty committed files. Only then may it stop corrected run
`jr_a97783e3...`; the checkpoint-producing unfixed run is never stopped.
It waits for the corrected worker to stop before launching once, using the
same dedicated fallback job and role, to
`concurrent-20261108/hardened-20261108/`. Success of the existing corrected run
cancels the handover. Missing/ambiguous checkpoints, stop errors or ambiguous
launches cause inspection instead of repeated paid launches.

The replacement has a 720-minute timeout, no automatic job retries, and uses
two G.8X workers. Startup, input downloads and score verification still run.
It can restore the previous implementation's complete network result using
only the explicitly pinned legacy code hash; every source identity, Parquet
checksum, independent network comparison, and actionable-case shape gate
remains enforced. It writes new checkpoints under its own code identity, never
overwriting the running producer's older namespace.

Hardened changes:

- Stage 6 uses a reusable candidate-link index instead of re-merging the full
  candidate table for every source batch. A completed payment scan is also
  checkpointed, so later timing/graph failures need not repeat the scan.
- Large raw network tables are released before model-view/dashboard scans.
- Account histories are streamed in bounded batches; exact preceding counts
  and the latest eight records are retained per case without loading a whole
  high-volume sender history.
- Model views, test-date breakdown, network-dashboard projection and exploration
  are independently checkpointed. These are bound to source/model identity,
  baseline dashboard, network proof, and hashes of every runtime module.
  Score and network verification still rerun before these caches are trusted.
- S3 requests have bounded connect/read timeouts and four standard attempts
  for transient failures. Export uploads still write `_SUCCESS` last.
- Original 32 MB dashboard/34 MB release limits and integrity checks remain;
  an oversized or invalid release is not silently truncated or published.

The actual packaged modules passed 84 focused tests, including restart reuse,
tampered-score rejection, checkpoint corruption, empty/boundary ring cases,
streamed history ordering, and guarded handover. Four shuffled input orders
with different batch sizes matched the exact previous network implementation;
the result is recorded in `legacy-parity.json`. New full HI-Large cloud
acceptance remains pending. Existing running jobs cannot load these changes
mid-run; they take effect in the controlled replacement.

## Oversized connected-component recovery

### Immediate concurrent backup supersedes the recovery watcher

At the user's request, the recovery watcher (PID 9328) was stopped and the
corrected backup launched immediately at 12:26 SGT. New run:
`jr_a97783e3fc816f109c66a55ad918e16a837232a65cb9fc328c6ec7816a591ea1`,
on the same isolated fallback job. Its concurrency limit is now two; both
workers use two G.8X instances and no automatic retries. No IAM change was needed.
The previous fallback remains running with the package it loaded at startup.

The new backup writes only to the existing allowed scratch subtree
`concurrent-20261108/backup-now-20261108/`. It loads the latest committed
checkpoint available when network analysis begins, with checkpoint writes
disabled to avoid racing the original fallback. At launch only the link
checkpoint was complete, so it may repeat the remaining network stages;
it cannot resume an incomplete payment pass. Plan, immutable artifact identity,
launch lock, and current run record are in
`output/oversized-export-backup-now-20261108/`.
Do not restart the older waiting watcher. The preceding prepared recovery
policy is incorporated into this immediate backup. Successful cloud acceptance
and publication remain pending. Concurrent-launch and checkpoint-read tests
passed (24 focused tests).

The main export retry failed at 12:11 SGT on 8 November: one connected
component contained 26,370 accounts, above the unchanged 19,277-account case
limit. The concurrent fallback remains useful because it saves an independent,
identity-bound complete network checkpoint before applying the shape gate.

`scripts/ingestion/recover-oversized-export.py` prepares a separate recovery
policy, enabled only for an isolated checkpointed export with
`--OVERSIZED_COMPONENT_REVIEW=true`. It first verifies the complete original
network against the Spark tables. It then withholds whole oversized components
from the actionable rings, ranking, graphs, and ring-coverage measures. It
does not split components, increase the limit, tune detection on labels, or
discard transactions. Full-population hubs, loops, pairs and population totals
retain their original scope. The unchanged shape gate checks the remaining
actionable case list. Original oversized memberships, edges and payments are
saved under private `evidence/oversized-networks/`; the proof and dashboard
disclose the number of withheld groups and accounts.

Prepared package and watcher state are in
`output/oversized-export-recovery-20261108-b/`. The prepared immutable artifact
is below the existing isolated fallback deployment prefix, with identity
`component-review-a15eda777644b7c2e77bdb7061dfc9c2d79ff101b97ed2bf5b972440dfa8f193`.
It preserves `network.py` byte for byte, allowing the active checkpoint to be
reused. The pinned deployed dashboard JS receives only the review disclosure.

The hidden local watcher waits for concurrent run `jr_fcc2ed54...` to stop.
Only its exact oversized-ring failure, a complete matching result checkpoint,
no remaining active job workers, and an empty scratch export destination allow
one corrected paid launch. It updates only that isolated fallback job's future
definition after the worker stops. Source success, unrelated errors, partial
outputs, missing checkpoints, or ambiguous launches do not trigger a retry.
An exclusive lock prevents duplicate watcher launches. The laptop must stay
awake and connected; inspect `status.json`, `watcher.log`, `watcher.err`, and
`watcher.pid` in that directory. No release is automatically published.

Validation: 60 source tests passed; 49 recovery/export/release tests passed;
41 tests passed against the actual packaged modules, including restoration
from checkpoints and preservation of original detection tables. Both local
and deployed-base dashboard JavaScript passed syntax checking. Cloud acceptance
of the corrected export remains pending.

## Concurrent launch supersedes the waiting watcher

At the user's explicit request, the local watcher was stopped and a concurrent
backup was confirmed RUNNING at 10:37 SGT. The original remains running.
New job: `is459-g3-ingest-export-fallback-a8b3b9b43adf`.
Run: `jr_fcc2ed54c482188a96ecbd9fa029c43c5c9c464c0667cbcb217c7f6380c0cdff`.
An earlier launch failed before compute because the production role's trust
policy restricts exact job names. A dedicated role,
`is459-g3-export-concurrent-20261108`, now trusts only the concurrent backup job
and permits reads of this Large run and its pinned deployments, writes to its
scratch output/checkpoints, and its log group. The production IAM role is unchanged.
Plan and launch record: `output/export-concurrent-20261108/`.

The backup writes exclusively to
`s3://is459-g3-588738581042-ap-southeast-1/tmp/ingest/recovery-exports/hi-large-delta1-20261107-batch/concurrent-20261108/`.
Its output is private scratch and cannot be automatically published by the
original workflow. Do not restart the old watcher or launch the old waiting job.
The existing original export prefix is untouched. A focused suite of 29 tests
passed, including a test with existing canonical output that confirms recovery
writes only to its own scratch prefix.

The remaining sections describe the earlier waiting configuration for context;
that watcher is no longer active.

The original `is459-g3-ingest-export` run
`jr_9abd95d24bab1111713cfdab523c4144ae7f723317fdec528a2c009e0436fa6e`
is left running. Its output belongs to `hi-large-delta1-20261107-batch`.

Prepared separate job: `is459-g3-ingest-export-fallback-f67a4b1a400d`.
It uses the existing role, pinned wheels/dashboard files/model, two G.8X
workers, a 360-minute timeout, and no automatic retries. The package preserves
unrelated modules from the original deployed recovery zip; only network,
checkpoint, export, and staging modules are replaced. The shared job definition,
workflow, deployment pointer, and IAM policies are unchanged.

The hidden local watcher runs `scripts/ingestion/export-fallback.py watch`
with profile `is459-g3-tools`. The machine must remain awake and connected.
The process ID, logs, prepared plan and current status are under
`output/export-fallback-20261108/`. It checks the original every minute.

- Original succeeds: exit without starting a fallback.
- Original fails or times out: check every job for active export owners and
  require an empty destination; start the fallback once using the original
  run arguments and retained stage inputs.
- Original stops manually, or partial export files exist: do not start.
- A watcher/API error or ambiguous launch: record the error and stop; inspect
  Glue before taking any further launch action. The exclusive `watch.lock`
  deliberately prevents accidental duplicate watchers or launches.

The fallback logs network subphases and row counts, and emits Python stack
traces every five minutes so long pandas operations remain diagnosable.
Three-account-loop candidates are computed in bounded batches with the same
directed-loop semantics. No verification or shape checks are disabled.

Private Parquet checkpoints for completed link aggregation and complete
network recomputation live under
`tmp/ingest/export-checkpoints/<run_id>/<identity>/`. Identity binds the source
reference hash, run manifest and network code. Each file has a SHA-256 hash;
commit metadata is written last. Incomplete or corrupted checkpoints cannot
be reused. Restored network results still undergo network parity and shape
checks before export. Checkpoints cannot recover work from the unmodified
original run.

The fallback does not publish or repair the failed Step Functions execution.
After it succeeds, inspect proofs, release identity and `_SUCCESS`, then use
the established recovery/publication procedure. Never silently publish an
unverified fallback result or overwrite partial export files.

Validation: 400 tests passed and one skipped in the ordinary non-Spark suite;
six additional fallback-guard tests passed. The packaged deployed-source
overlay passed 38 focused tests. Full HI-Large cloud acceptance remains pending.
