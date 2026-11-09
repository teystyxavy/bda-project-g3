# Project documentation

Start with the deployment status for what is live today, then the runbook for the
task at hand. Last reviewed **9 November 2026**.

## Start here

| Topic | Entry point |
|---|---|
| What is deployed, recent milestones, drift and open items | [Deployment status](aws-deployment-status.md) |
| Current AWS architecture (5 pages: overview, run, BQ1, BQ2, streaming) | [`docs/is459-g3-aml-pipeline-aws.drawio`](../docs/is459-g3-aml-pipeline-aws.drawio) |
| Business questions, schema, features and modelling policy | [Pipeline plan](big-data-pipeline-plan.md) |
| Deploy every stack from zero | [Deployment guide](../DEPLOYMENT.md) |
| Repository commands by category | [Script guide](../scripts/README.md) |
| Terraform stacks | [Infrastructure guide](../infra/README.md) |

## Runbooks

| Topic | Runbook |
|---|---|
| Run ingestion, training and recovery | [Ingestion runbook](runbooks/ingestion-runbook.md) |
| HI-Large with the delta1 model (outcome and plan) | [HI-Large runbook](runbooks/hi-large-delta1.md) |
| HI-Large export recovery log, 8 Nov | [Export fallback](runbooks/export-fallback-20261108.md) |
| HI-Medium runs and evidence | [HI-Medium runbook](runbooks/hi-medium.md) |
| BQ2 mule networks: method and results | [BQ2 mule networks](runbooks/bq2-mule-networks.md) |
| BQ2 network job deployment | [BQ2 deployment](runbooks/bq2-mule-network-deployment.md) |
| Batch caching and parallel branches | [Optimisation implementation](runbooks/pipeline-optimisation-implementation.md) |
| Glue fixtures and batch verification | [Batch runbook](runbooks/aws-batch-runbook.md) |
| Glue stage migration (1 Nov) | [Glue migration](runbooks/glue-stage-migration.md) |
| Dashboard BQ1/BQ2 exploration coverage | [Dashboard exploration](runbooks/dashboard-exploration.md) |
| Kaggle variants, sizes and costs | [Download options](runbooks/kaggle-download-options.md) |
| AWS profile setup | [Login guide](runbooks/aws-login.md) |
| 1 Nov cleanup and archive | [AWS cleanup](runbooks/aws-cleanup-20261101.md), [S3 archive](runbooks/s3-archive-20261101.md) |
| Streaming replay, parking and restore | [Streaming README](../streaming/README.md) |

## Architecture and proposals

| Topic | Entry point |
|---|---|
| Diagram sources and exported images | [Architecture guide](architecture/README.md) |
| Remaining optimisation ideas | [Optimisation proposal](proposals/pipeline-optimisation-proposal.md) |
| Streaming scores on the dashboard | [Kinesis dashboard proposal](proposals/kinesis-dashboard-improvements.md) |
| BQ1 model performance research | [Model performance proposal](proposals/model-performance-improvement-proposal.md) |
| Receiver-side behaviour features | [Improvement #1 spec](proposals/improvement_1_spec.md) |
| Dataset-subset literals in many files | [Dataset contract refactor](proposals/dataset-contract-refactor-proposal.md) |
| Original ingestion and SageMaker plan | [Ingestion and SageMaker plan](proposals/ingestion-and-sagemaker-plan.md) |

## Evidence

`evidence/` holds sanitized milestone records; see its [README](evidence/README.md).
Complete private run outputs stay in the ignored `output/` folder.

## Archived material

The October implementation reviews (`planning/archive/`) and the presentation
material (`planning/delivery/`) were removed from the repository in November. Links
to them in older notes are marked *(archived)* or *(retired)*. The reviews are kept
outside Git in `PROJECT\archive\archive\`, the presentation material is in
`PROJECT\Delivery\`, and both remain in the Git history.
