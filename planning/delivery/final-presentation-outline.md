# Final presentation content outline

Use with [the demonstration runbook](final-demo-runbook.md). This is content for
the final presentation, not a finished PPTX/PDF. The proposal remains historical.

| Slide | Main content | Evidence / demo |
|---|---|---|
| 1. Compliance and treasury decisions | Group 3, project objective, one decision for each audience | Business questions and intended actions |
| 2. Dataset and historical FX | 5,078,345 transactions; 5,177 positive labels; 15 currencies; synthetic-data scope | Kaggle, Frankfurter and Yahoo source links; September 1–18 activity with sparse tail separated |
| 3. Implemented architecture | Local ingestion/FX preparation to private S3, Glue curation, catalog projection, AWS inference/Athena, SageMaker verification/export and private-origin CloudFront dashboard | New `../architecture/automated-ingestion.drawio`; earlier `../architecture/aml-pipeline-architecture.drawio`; separate `../architecture/aml-pipeline-aws-icons.drawio` enhancements; reviewed embedded-XML PNGs |
| 4. Curation and temporal correctness | Duplicate account headers read by position, composite joins, string IDs, preceding windows, same-day treasury versus prior-day AML FX, date partitions | Verified full curation excerpt |
| 5. Model evaluation | Chronological split, frozen validation-selected depth 5, documented rule baseline; test precision@100 44% versus 0%; recall@100 4.60%; AP 0.135546 | Frozen comparison and full cloud score reconciliation |
| 6. Investigation queue | Test-period top-100 queue and signed model explanations; reviewer chooses which cases to investigate | Dashboard clip/demo; ranking scores and synthetic limitations visible |
| 7. Corridor concentration | USD 1.748 trillion all-period volume; 6.18% bank-pair top-five share; daily September 6 versus 8 variation | Treasury dashboard, clear metric grain, no AML inference from volume |
| 8. Batch orchestration | Verified full source-to-dashboard workflow: 28m34s, including baseline checks, frozen scoring, Athena, optional candidate training and publication | New Step Functions success, 5,078,345 / 1,082,185-row reconciliation, SageMaker candidate completed in 159 billable seconds |
| 9. Bounded streaming | One 884-event cohort, 32 sender histories, transactional state plus audit, exact feature parity, Disabled consumer | Completed replay evidence; actual elapsed 1,049.545 seconds |
| 10. Operations, cost and limitations | Compute estimates versus invoices; course USD 250, monthly USD 10 awareness alert; retained shard USD 0.0184/hour; synthetic data and image scan limitations | Timestamped evidence, separately approved cleanup plan after presentation |
| 11. Individual contributions | Each actual member, concrete component/files/work and speaking role | Await team confirmation; no inferred authorship |
| 12. Artefact appendix | Setup/run YouTube links, source, diagrams, runbooks and public dashboard | Check links/access before Week 13 submission |

## Proposal claims to replace

| Proposal statement | Verified implementation wording |
|---|---|
| Risk probability | Uncalibrated ranking score |
| Frankfurter / ECB only | Frankfurter plus Yahoo historical rates for three uncovered currency histories |
| Glue crawlers | Explicit catalog schema and date partition projection |
| Cloud model training and model.joblib | Local frozen XGBoost training; UBJ model with hashed schema/manifest; AWS inference |
| EventBridge starts scheduled batch runs | No schedule is deployed. An opt-in EventBridge rule starts the batch when a laptop upload writes `raw/trigger.json` (deployed 6 Oct; not yet test-run) |
| Workflow covers ingestion, ETL, training and publication | Reviewed workflow consumes verified curated data and runs frozen scoring plus treasury SELECT |
| SNS alerts on high-risk transactions | SNS monitors processing failures; no transaction alert threshold exists |
| Live dashboard | Published static snapshot with interactive exploration |
| Run Kinesis only during demos | Consumer is disabled after acceptance; provisioned shard remains deployed and billable |
| USD 150/200/230 budget alerts | Verified USD 10 monthly alert with 50/80/100% notifications; USD 250 total course allowance |
| Streaming finishes in about 89 seconds | Actual 884-event replay took 1,049.545 seconds; throughput/latency claims remain bounded |

## Contribution table to fill from actual work

| Member | Actual contribution and supporting files | Component presented / demonstrated |
|---|---|---|
| Awaiting team details | Awaiting team details | Awaiting team details |

Add one row per actual member. Every member must present/demo at least one
component. Do not substitute suggested speaking roles for development attribution.
