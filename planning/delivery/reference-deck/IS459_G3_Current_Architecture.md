# Current project architecture

Evidence date: 6 October 2026. Open [the editable three-page diagram](IS459_G3_Current_Architecture.drawio) or use slides 7, 8 and 26 of [the visual project guide](IS459_G3_Visual_Project_Guide_v3.pptx).

## Page 1: AWS batch architecture

Lambda acquires the Kaggle source files and historical FX, then writes a traceable manifest into private S3. Glue curates the records, computes features from preceding events, and writes Parquet partitioned by transaction date. The analysis and verification stage creates both business outputs before Lambda publishes the verified HTML, CSS, JavaScript and JSON files. CloudFront serves the private S3 origin.

Arrows show the logical movement of data and artifacts. The top row runs from sources to curated data, then the bottom row returns from business analysis to the dashboard. Step Functions controls stage dependencies and failure handling; it does not carry the transaction files. The diagram combines logical S3 zones for readability rather than asserting a specific number of buckets.

## Page 2: Parallel business branches

Curated records feed three parallel branches:

| Branch | Purpose | Business question |
| --- | --- | --- |
| SageMaker Processing | Independently verify source records, FX and histories; compute the rule baseline | AML review |
| Glue scoring | Apply the selected XGBoost model with its saved feature schema | AML review |
| Athena | Aggregate directed bank and currency corridors using same-day USD amounts | Treasury exposure |

SageMaker export independently reconciles the scored population and corridor totals before publication. Optional candidate training follows the verified baseline, retains private artifacts and never promotes automatically. Stage reuse preserves full verification for each new release.

## Page 3: Optional streaming demonstration

A bounded CSV replay sends ordered sender events into Kinesis. Lambda recomputes the features and frozen-model score, reads and atomically updates history/results in DynamoDB, and writes audit evidence to S3. DynamoDB and S3 are separate Lambda outputs. State commits before audit; duplicate delivery can repair missing audit evidence.

The retained acceptance includes 884 results and 32 sender histories. The consumer remains disabled after acceptance. This path demonstrates event/history parity and does not update the batch dashboard. SNS reports processing failures. No live transaction-alert threshold is configured.

## Source documents

- [Project overview](../../../README.md)
- [Ingestion and operating runbook](../../runbooks/ingestion-runbook.md)
- [Current optimized implementation](../../runbooks/pipeline-optimisation-implementation.md)
- [Streaming demo guide](../../../streaming/README.md)

No recurring batch schedule is active. Both business questions are answered by the batch backbone. The separate streaming demonstration is optional.
