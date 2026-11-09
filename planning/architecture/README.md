# Architecture sources

## Current

- **[`docs/is459-g3-aml-pipeline-aws.drawio`](../../docs/is459-g3-aml-pipeline-aws.drawio)**
  is the current AWS architecture (updated 9 November 2026). Five pages:
  1. Overview: dashboard control, cloud acquisition with the result cache, the
     `is459-g3-ingest` batch, and the parked streaming path with its
     dashboard publisher.
  2. Pipeline run: every Step Functions state from raw files in S3 to publication,
     including resume mode, export checkpoints and the main/demo dashboard pointer.
  3. BQ1 zoom: scoring and the export double-check.
  4. BQ2 zoom: mule-network hubs, loops, pairs and rings, and the oversized-component review.
  5. Streaming: Kinesis replay, consumer with sender and receiver state, and the
     publisher behind the **Incoming AML** tab.
- [Dashboard ingestion flow](dashboard-ingestion-flow.md) and its
  [editable diagram](dashboard-ingestion-flow.drawio): the service map shown in the
  live Ingestion tab.

The deployed workflows are defined in `infra/cloud-ingestion/workflow.asl.json` and
`infra/ingestion/workflow.asl.json`. When a diagram and those files disagree, the
workflow files win.

## Earlier milestones

These diagrams record October milestones and are kept for the record. Their guides
say what has changed since.

- [Automated ingestion](automated-ingestion.md) and its
  [editable diagram](automated-ingestion.drawio): the local-acquisition predecessor.
- [Core business views](aml-pipeline-architecture.md) and its
  [editable diagram](aml-pipeline-architecture.drawio): BQ1 and the original treasury BQ2.
- [Additional enhancements](aml-pipeline-aws-icons.md) and its
  [editable diagram](aml-pipeline-aws-icons.drawio): the 6 Oct orchestration and streaming milestones.
- `exports/`: reviewed PNG exports referenced by evidence records.

The original proposal diagrams and the November reference deck were removed from the
repository; see [archived material](../README.md#archived-material).

`scripts/architecture/build-architecture-diagrams.py` and
`scripts/architecture/export-architecture.ps1` use this directory. Follow the
project-local architecture skill before changing diagrams.
