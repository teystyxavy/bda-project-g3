# Architecture sources

- [Current batch architecture](../delivery/reference-deck/IS459_G3_Current_Architecture.md) and
  [editable diagram](../delivery/reference-deck/IS459_G3_Current_Architecture.drawio): page 1 includes
  the opt-in EventBridge upload trigger (deployed 6 Oct 2026, not yet test-run).

- [Dashboard ingestion flow](dashboard-ingestion-flow.md) and
  [editable diagram](dashboard-ingestion-flow.drawio): AWS service phases shown in
  the live Ingestion tab; new dashboard controls are locally implemented, deployment pending.

- [Automated ingestion](automated-ingestion.md) and
  [editable diagram](automated-ingestion.drawio): source-to-dashboard batch flow.
  The guide links to the later optimisation changes.
- [Core business views](aml-pipeline-architecture.md) and
  [editable diagram](aml-pipeline-architecture.drawio): earlier verified BQ1/BQ2 milestone.
- [Additional enhancements](aml-pipeline-aws-icons.md) and
  [editable diagram](aml-pipeline-aws-icons.drawio): orchestration and streaming milestones.
- `exports/`: retained reviewed PNG exports.
- [Original proposal diagrams](../archive/architecture-proposal/README.md): historical designs.

The latest parallelism/cache behavior is documented in the
[optimisation implementation](../runbooks/pipeline-optimisation-implementation.md)
and defined by `infra/ingestion/workflow.asl.json`. Diagram milestone descriptions
do not replace the deployed workflow or current runbook.

`scripts/architecture/build-architecture-diagrams.py` and `scripts/architecture/export-architecture.ps1`
use this directory. Follow the project-local architecture skill before changing diagrams.
