# Verified bounded streaming demo — 6 October 2026

The one approved replay of **884 transactions across 32 complete sender histories**
passed cloud acceptance. Lambda consumption is **Disabled**, all fourteen Terraform
resources remain deployed, and the final refresh plan reports no differences.
No second replay, retraining, schedule, dashboard publication or cleanup occurred.

## Acceptance evidence

| Check | Observed result |
|---|---|
| Producer receipts | 884 unique transaction identities; increasing Kinesis sequence numbers |
| DynamoDB | 916 items: 884 unique results plus 32 sender states |
| Sender state | Every sender version equals its expected transaction count |
| S3 audit | 884 objects; every downloaded payload exactly equals its DynamoDB result |
| Frozen reference | Exact schema, inputs, IDs and behaviour features for all 884 results |
| Model scores | Maximum absolute difference 2.9103830456733704e-11; within rtol 1e-6 / atol 1e-7 |
| Failure archive | Zero records in the mapping's isolated S3 failure prefix |
| Monitoring | Zero runtime errors, record failures, Lambda Errors and Throttles |
| Encryption | Both audit samples AES256; failure bucket AES256 with all four public access blocks |
| Shutdown | Mapping Disabled; only enabled true → false changed; no resource deletion |
| Final Terraform refresh | Exit code 0; no changes |

Account `588738581042`, region `ap-southeast-1`, run `stream-smoke-20261006`.
Mapping UUID `7f935d6a-04f2-4fd4-9310-261f2eea68dc`.
The image resolves to the reviewed digest
`sha256:7430b609e68fc7c3c44c3ebf158909e79ba223d6f9aaac5eaf0f57ff99697130`.
The model and prepared cohort remained frozen throughout.

The producer ran from 00:10:50.0536452 to 00:28:19.5983852 UTC on 6 October
(08:10:50 to 08:28:19 Singapore time), taking **1,049.545 seconds**.
The configured ten-event/second cap does not guarantee that throughput:
sequential service acknowledgements lengthened the replay. This duration is not
a measurement of live ingestion latency. Original historical transaction times
are preserved, with verified curated inputs and existing prior-day FX.

## Retained evidence and cost

[Machine-readable deployment result](../../evidence/streaming-deployment-result-20261006.json)
records counts, monitoring, shutdown and hashes. The local all-row parity report
is `output/streaming-cloud-verification-20261006.json`. Prepared input evidence
under `output/streaming-demo-20261006/` remains immutable historical preparation.

Private cloud evidence is under `%LOCALAPPDATA%/IS459-G3/`, outside OneDrive:

- `streaming-reconciliation-20261006/evidence.json` and all 884 downloaded audits.
- `streaming-receipt-20261006.jsonl`, producer result and replay window.
- `streaming-operations-final-20261006.json` with logs, metrics and encryption samples.
- `streaming-disabled-mapping-20261006.json` and shutdown plan/apply evidence.
- `streaming-final-drift-20261006.*` and `streaming-retained-disabled.tfvars.json`.

Monitoring captured 715 invocation reports and 86,085 billed milliseconds.
At 2 GiB and the reviewed compute rate, estimated Lambda compute is
**USD 0.0028695**, excluding requests, storage, Kinesis, other services and taxes.
This estimate is not an invoice. Disabling consumption retains the provisioned
shard, which costs about **USD 0.0184/hour**, or USD 13.69 per 744 hours,
until separately authorized cleanup. The USD 10 budget alert is not a spend cap;
the course budget remains USD 250. Retain explicit current variables in any
future Terraform plan: default-off flags would propose deleting populated resources.

## Demonstration scope and limitations

The demonstration serves the frozen BQ1 ranking model and recomputes preceding
sender behaviour in Lambda. Its result supports the bounded selected cohort;
it does not establish full-population streaming performance, production load
capacity or real-world AML validity. The batch pipeline still answers both
business questions independently. The published dashboard remains a static batch
snapshot. SNS covers processing failures; no transaction alert threshold is set.

ECR's completed basic OS-package scan has **8 HIGH, 6 MEDIUM and 1 LOW findings**.
The bounded-use assessment is retained in the
[failure-isolation review](streaming-failure-isolation-review-20261006.md).
These findings were not fixed, and the scan is not a complete Python dependency
audit. Patch/rebuild and repeat parity/security review before broader reuse.

Local acceptance instructions are in [streaming/README.md](../../../streaming/README.md).
Another cloud replay, image push, schedule or destructive cleanup needs explicit
authorization.
