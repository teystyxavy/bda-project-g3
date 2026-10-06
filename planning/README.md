# Project documentation

Start with the current operating instructions and measured evidence. Historical
reviews retain the decisions and results from earlier milestones.

| Topic | Entry point |
|---|---|
| Repository commands by category | [Script guide](../scripts/README.md) |
| Transaction-level exploration | [Notebook guide](../notebooks/README.md) |
| Business questions, schema and modelling policy | [Pipeline plan](big-data-pipeline-plan.md) |
| Deployed resources and cost notes | [Deployment status](aws-deployment-status.md) |
| Run ingestion, training and recovery | [Ingestion runbook](runbooks/ingestion-runbook.md) |
| Full batch optimisations and caching | [Implementation](runbooks/pipeline-optimisation-implementation.md) |
| Latest measured acceptance | [Optimisation result](evidence/pipeline-optimisation-result-20261006.json) |
| AWS profile setup | [Login guide](runbooks/aws-login.md) |
| Glue fixtures and batch verification | [Batch runbook](runbooks/aws-batch-runbook.md) |
| Cloud acquisition and end-to-end flow | [Automated architecture](architecture/automated-ingestion.md) |
| Editable diagrams and rendered images | [Architecture guide](architecture/README.md) |
| Presentation and demonstration | [Delivery guide](delivery/README.md) |
| Remaining optimisation ideas | [Proposal](proposals/pipeline-optimisation-proposal.md) |
| Kinesis scores flowing into the dashboard | [Proposed streaming dashboard connection](proposals/kinesis-dashboard-improvements.md) |
| Earlier implementation reviews | [Historical archive](archive/README.md) |

`evidence/` contains sanitized milestone summaries; complete private run outputs
remain under ignored `output/`. Recorded timestamps and run IDs distinguish
preparation checks from actual cloud execution. Evidence JSON is retained unchanged
when reorganised; original path/hash fields describe the layout at verification time.
