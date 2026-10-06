# Architecture sources

- [Automated ingestion](automated-ingestion.md) and
  [editable diagram](automated-ingestion.drawio): source-to-dashboard batch flow.
  The guide links to the later optimisation changes.
- [Core business views](aml-pipeline-architecture.md) and
  [editable diagram](aml-pipeline-architecture.drawio): earlier verified BQ1/BQ2 milestone.
- [Optional enhancements](aml-pipeline-aws-icons.md) and
  [editable diagram](aml-pipeline-aws-icons.drawio): orchestration and streaming milestones.
- `exports/`: retained reviewed PNG exports.
- [Original proposal diagrams](../archive/architecture-proposal/README.md): historical designs.

The latest parallelism/cache behavior is documented in the
[optimisation implementation](../runbooks/pipeline-optimisation-implementation.md)
and defined by `infra/ingestion/workflow.asl.json`. Diagram milestone descriptions
do not replace the deployed workflow or current runbook.

`scripts/build-architecture-diagrams.py` and `scripts/export-architecture.ps1`
use this directory. Follow the project-local architecture skill before changing diagrams.
