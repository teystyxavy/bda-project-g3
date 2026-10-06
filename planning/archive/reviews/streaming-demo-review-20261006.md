# Streaming demo deployment review — 6 October 2026

Status: **approved replay completed and verified; consumer Disabled**. Fourteen
resources and the exact image are deployed. All 884 results and 32 sender
histories reconciled, with no errors, throttles or failure archive records.
See [the completed result](streaming-demo-result-20261006.md) and
[revised failure-isolation review](streaming-failure-isolation-review-20261006.md).
Sections below retain the
original eleven-resource proposal and pre-deployment evidence; the revised
review supersedes its destination policy and final resource count.
Account `588738581042`, Singapore `ap-southeast-1`, run
`stream-smoke-20261006`. The completed batch pipeline and published dashboard
remain the verified business outputs. This optional demo serves the frozen BQ1
model; it does not rerun training, batch scoring, Athena or publication.

## Concrete local result

`scripts/prepare-streaming-demo.py` selected 32 senders using composite
bank/account hashes and total-history counts, without consulting labels. It
includes all 884 events for those senders across the dataset calendar, rather
than truncating a busy account's history. Original transaction timestamps are
preserved; the producer caps sending at 10 events/second. The actual approved
replay took 1,049.545 seconds because it waits for sequential acknowledgements.
This is a verified **curated CSV replay**, with existing prior-day FX values,
not a raw ingestion/FX-fetching service or measured live latency demonstration.

Sixty-four targeted Python tests pass with one environment-dependent skip;
eleven core/SDK tests pass in the final Lambda image. Every rolling feature
agrees exactly with the frozen batch reference. Every
model score passes `rtol=1e-6, atol=1e-7`, both natively and in the final Linux
Lambda image. Duplicate delivery, same-timestamp peers, window boundaries,
ordering failures, state-capacity limits, durable S3 repair and actual boto3
transaction serialization are tested. Cloud acceptance has separate tests for
missing, duplicate and altered audit records. Fixtures are local evidence only.

Evidence: `output/streaming-demo-20261006/verification.json`,
`final-linux-parity.json`, `final-linux-tests.txt`; image/context evidence under
`output/streaming-container-final-20261006/`. Generated outputs are gitignored.

| Reviewed item | SHA-256 |
|---|---|
| Frozen source | `04ef4a7564ab7635bba935f5ebf95566e3e6e922dc59f510763d88196b1e12f6` |
| Frozen artifact manifest | `323a1eb1555f587d6553f09b9b0b90a9f49030460986df1faaf8b55ef3fc1b8f` |
| Replay CSV | `41b2fd5b44f867050c7b936c082f836dd0df2a7af189ada99275acf8afd20b37` |
| Replay envelopes | `eee3089920d11d30429ba01cd2f78d9050c73a20a6886f14abfa85ed713dce8d` |
| Expected results | `a22e2a1cc51370819cba0307d8cf3d2d8a40deee3943bf6ecb2093e8e12df3c6` |
| Final local OCI image manifest | `7430b609e68fc7c3c44c3ebf158909e79ba223d6f9aaac5eaf0f57ff99697130` |

Use only image `is459-g3-streaming:final-20261006`; earlier local tags are
superseded. The image uses the pinned official Python 3.12 Lambda base,
`linux/amd64`, no multiarchitecture/provenance index, and CPU XGBoost 3.2.0.
Its single manifest is 2,387 bytes and local uncompressed size is
1,404,553,455 bytes. ECR retrieval, scanning and real Lambda initialization
remain unverified until an approved cloud deployment.

## Proposed resources and approval boundary

`infra/streaming/` is a separate opt-in Terraform stack, default off. Private
state and plans belong under `%LOCALAPPDATA%/IS459-G3`, outside OneDrive.

The final source defines **nine staging creates**: immutable encrypted ECR
repository and Lambda retrieval policy; one encrypted provisioned Kinesis
shard with 24-hour retention; one protected on-demand DynamoDB table;
seven-day Lambda log group; execution role and inline policy; record-failure
metric filter and alarm. An uploaded, digest-verified image allows **two more
creates**: Lambda and an initially disabled event-source mapping, eleven total.
Activation is a subsequent mapping update, requiring `replay_approved=true`.
No existing batch, hosting, budget or SNS resource is changed by this stack.

Lambda has 2 GiB memory, 60-second timeout, concurrency one and model threads
one. Mapping has batch size ten, parallelization one, one-second batching,
partial batch responses, one retry and one-hour maximum record age. It stops
at the first failed sequence instead of processing later records with missing
history. Exhausted deliveries use AWS's S3 failure archive with full invocation
payload. A poison event can block subsequent events from that sender; never
silently skip it and claim correct scores.

Terraform formatting/validation and five mocked infrastructure tests pass.
The renewed login allowed final authenticated plans: nine staging creates and
eleven image-pinned creates, with no updates/deletes and consumption disabled.
Private plans are `streaming-staging-final-20261006.tfplan` and
`streaming-disabled-final-20261006.tfplan` under the private state directory.
[Preparation receipts](../../evidence/streaming-preparation-result-20261006.json) record plan
hashes and resource addresses. Earlier plans are superseded. These plans have
not been applied; refresh/review drift before applying after approval.

Any requested cloud approval must name the following complete scope: nine
staging creates; exact verified image upload and scan/retrieval checks; two
disabled consumer creates; mapping activation; **one 884-event replay** at ten
events/second; read-only reconciliation; and disabling the consumer afterward.
No automatic replacement paid replay, schedule, transaction SNS messages,
dashboard publication or destructive cleanup is included. Any failed run is
preserved for diagnosis. Disabling the mapping does not stop shard billing.

## Correctness and permission review

State keys include run and hashed `(bank, account)`; bank normalization matches
batch logic and account leading zeros survive. State stores bounded exact
history and a version. A conditional two-Put DynamoDB transaction commits both
the advanced sender version and unique result. Same-timestamp peers are
excluded from preceding windows. A duplicate returns its stored result;
changed payload under the same identity fails. S3 audit follows the commit,
so redelivery can repair S3 without advancing history again. Existing bucket
versioning may retain repeated identical audit writes: logical idempotence does
not mean exactly one physical S3 request/version.

There is no TTL: historical event time differs from current expiry time.
Limits are 1,000 retained events and 128 KiB state, failing closed before the
400 KiB DynamoDB item limit. This selected cohort has at most 40 total events
per sender. This is not an unbounded account-state production design.

IAM Policy Autopilot 0.3.0 generated SDK baselines from `streaming/handler.py`
and `scripts/replay-streaming-demo.py` with region, account and Terraform path
hints. Evidence is `iam-handler-generated.json` and
`iam-producer-generated.json` in the local package. The attached template
refines those baselines: GetItem/PutItem on this table, audit PutObject only on
`streaming/stream-smoke-20261006/audit/*`, exact stream polling, and this log
group's stream/write actions. It removes unused delete/update, ACL/tagging,
replication, object-Lambda and customer KMS actions. Application DynamoDB/audit
actions require the unqualified `lambda:SourceFunctionArn`. Framework polling
and failure archives have separate statements because they lack that context.

Explicit exception: the failure destination needs ListBucket on the existing
project bucket and PutObject under AWS-controlled `aws/lambda/*` keys; the
mapping UUID does not exist at initial planning. Resource-account conditions
bind both to this account. The role can list project object names, but has no
object Get/Delete or raw, curated, hosting or model-artifact write permissions.
Standard Lambda service trust is used; `aws:SourceArn` is not invented as an
execution-role credential restriction. The ECR service retrieval policy names
this function/account. Default `alias/aws/kinesis` needs no custom key grant.
Access Analyzer returned no findings for the refined runtime policy. The ECR
policy names the exact repository resource, following the
[ECR resource-policy documentation](https://docs.aws.amazon.com/AmazonECR/latest/userguide/repository-policies.html).
Its final Access Analyzer check has zero findings, retained as
`ecr-policy-validation.json`.

CloudWatch/SNS reports structured record failures, not individual transaction
alerts. Cold-start initialization exceptions do not produce that custom metric;
check Lambda Errors, mapping state and failure archive during acceptance. Model
scores are ranking outputs, not calibrated probabilities; no rule-score-60
threshold or new-account heuristic is applied to frozen model scores.

## Costed bounds

Singapore public prices retrieved on 6 October are retained in
`output/streaming-demo-20261006/prices-singapore.json`. Estimates ignore free
allowances, taxes and unrelated account traffic; they are not invoices or caps.

| Item | Price / estimate |
|---|---|
| One provisioned shard | USD 0.0184/hour; USD 13.6896 for 744 hours |
| Kinesis writes | USD 0.0000000195 per 25 KiB payload unit |
| Lambda x86 compute / requests | USD 0.0000166667/GB-second; USD 0.0000002/request |
| DynamoDB standard writes / reads | USD 0.71/million write units; USD 0.1425/million read units |
| ECR storage | USD 0.10/GB-month; about USD 0.14/month using uncompressed local size as a conservative size estimate |
| Custom metric / standard alarm | USD 0.30 / USD 0.10 per month, prorated; metric only emits on failures |

Allow **USD 0.10–0.50** for one hour of deployment/replay/verification, including
small request/log/storage overhead. This is an estimate, not an enforced spend
limit. About 90 batches at 2 GiB and three seconds would cost roughly USD 0.009
Lambda compute; a conservative 180 full 60-second invocations would be about
USD 0.36. DynamoDB transactional writes consume two units per KiB per item,
for both state and result; actual usage depends on sizes and retries. S3
requests, archives and versions also cost money. No provisioned concurrency,
customer KMS key, managed server or Marketplace product is added.

Leaving the shard deployed adds about **USD 13.69/month**, even with the
consumer disabled, plus storage/monitoring and requests. That exceeds the
project's USD 10 monthly alert on its own; the alert is awareness, not a cap.
The course's USD 250 total budget still applies. Cleanup needs separate explicit
authorization; do not promise that resources expire automatically.

Prices: [Kinesis](https://aws.amazon.com/kinesis/data-streams/pricing/),
[Lambda](https://aws.amazon.com/lambda/pricing/),
[DynamoDB](https://aws.amazon.com/dynamodb/pricing/on-demand/),
[ECR](https://aws.amazon.com/ecr/pricing/),
[CloudWatch](https://aws.amazon.com/cloudwatch/pricing/).

## Cloud acceptance after approval

Verify published ECR digest equals the reviewed local manifest, inspect scan
findings, check Lambda Active and actual frozen artifact loading, and confirm
fresh run-scoped table/audit output before enabling consumption. Retain the
producer receipt outside OneDrive. Download only this run's audit prefix.
`scripts/verify-streaming-demo.py` requires exactly 884 unique expected
identities, exact fields/features/input hashes/run IDs, frozen-score tolerance
and a complete receipt; it hashes every accepted audit file. Also inspect all
32 sender versions, Lambda/mapping errors and AWS failure archives. Confirm
empty failures and disable the consumer after the bounded replay. Capture
post-apply drift, execution/usage evidence and actual AWS outputs before
changing this document to cloud-verified status.
