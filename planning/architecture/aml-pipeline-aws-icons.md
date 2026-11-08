# Implemented additional enhancements

Editable source: [aml-pipeline-aws-icons.drawio](aml-pipeline-aws-icons.drawio).
The two pages separate guarded orchestration and bounded streaming from the
[core batch paths](aml-pipeline-architecture.drawio). The batch backbone answers
both business questions independently. Sources reflect verified 6 October
milestones. Original proposal sources are archived separately.

## Page 1: guarded batch orchestration

1. An explicitly approved operator supplies the reviewed run ID and fresh output
   prefixes to the Standard Step Functions workflow.
2. The workflow validates request/job/table/workgroup contracts and rejects
   existing scored or temporary output.
3. Parallel branches run frozen Glue scoring and the Athena treasury SELECT over
   the existing verified curated data.
4. The workflow checks the real success marker and returns business outputs ready.
   The 232.469-second successful execution produced 5,078,345 reconciled scores
   and 1,082,185 reconciled daily bank/currency corridor rows.
5. Local acceptance remains a prerequisite for any separately reviewed dashboard
   publication. The workflow does not perform ingestion, curation, training,
   export or publication. No schedule is deployed.
6. Catchable failures notify the SNS failure topic and then fail. Read/notification
   operations retry; compute tasks do not automatically retry. That failure route
   has local test coverage but was not triggered by the successful AWS execution.

## Page 2: bounded streaming replay

1. The local producer sends 884 verified curated transactions from 32 complete
   sender histories, preserving historical event times and prior-day FX.
   Payloads exclude labels and precomputed rolling features.
2. One provisioned Kinesis shard uses a composite sender partition key. Its
   Lambda event source mapping delivers records to the frozen image/model.
3. Lambda recomputes strictly preceding windows and scores with the frozen model.
   A conditional two-Put DynamoDB transaction commits the state and unique result.
4. Lambda writes an encrypted S3 audit result. The main line through DynamoDB
   and S3 denotes commit-then-audit order; Lambda performs both writes.
   Durable duplicates repair missing audit without advancing sender state.
5. Local full reconciliation checks 884 exact feature results and score tolerance,
   32 expected sender versions, 916 total DynamoDB items and 884 identical audits.
6. Consumption is Disabled after acceptance; the final Terraform refresh has no
   differences. Resources remain retained, and the shard stays billable at about
   USD 0.0184/hour until separately authorized cleanup.

The downward Kinesis failure arrow represents the **Lambda event source mapping's**
configured S3 on-failure destination, not a direct Kinesis bucket write. The
isolated private bucket has zero archived records. CloudWatch receives consumer
logs and tracks structured failures; an alarm targets SNS for processing failures.
No transaction-level threshold or live dashboard update exists.

## Key design decisions and limits

| Component | Purpose and verified scope |
|---|---|
| Step Functions | One approved guarded full-population scoring/treasury run |
| Kinesis | Single provisioned shard for the single completed cohort |
| Lambda | Frozen ECR image, one consumer, bounded ordered historical state |
| DynamoDB | Pay-per-request durable state and unique results, conditional transactions |
| S3 audit / isolated failure bucket | Separate successful audit and failed-batch destinations |
| CloudWatch / SNS | Processing logs, alarms and failure notifications |

The replay took 1,049.545 seconds; it does not establish production throughput
or end-to-end live latency. ECR's OS-package scan has 8 HIGH, 6 MEDIUM and 1 LOW
unresolved findings. The bounded-use assessment does not repair those packages.
Patch/rebuild and review before broader reuse. No second replay is authorized.

The AWS icons follow the project skill. Specific Glue, Athena, Kinesis and
CloudWatch stencils were verified against the official offline library and
visually reviewed in local PNG exports. Solid arrows show the implemented path. Red dashed arrows show
configured failure routes that remained untriggered in the successful runs.

Evidence: [orchestration result](../evidence/orchestration-result-20261006.json),
[streaming result](../evidence/streaming-deployment-result-20261006.json), and
[streaming acceptance review](../archive/reviews/streaming-demo-result-20261006.md).
Rebuild instructions are in [the core diagram guide](aml-pipeline-architecture.md).
