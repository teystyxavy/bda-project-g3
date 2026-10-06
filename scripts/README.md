# Script guide

Run scripts from the repository root. Locations remain stable so documented
commands and container preparation continue to work. Scripts are grouped here
by purpose; argument details are available through Python `--help` or the runbooks.

| Task | Entry points |
|---|---|
| Authenticate and run the deployed pipeline | `configure-aws.ps1`, `ingest.ps1` |
| Inspect sources/progress/publication | `inspect-cloud-acquisition.py`, `inspect-ingestion.py`, `verify-ingestion-release.py` |
| Measure elapsed times and cache reuse | `benchmark-ingestion.py` |
| Prepare/update ingestion deployment | `prepare-ingestion.py`, `prepare-sagemaker-container.py`, `update-ingestion.ps1` |
| Initial cloud deployments | `deploy-ingestion.ps1`, `deploy-cloud-ingestion.ps1` |
| Local source acquisition | `kaggle-download.py`, `fetch-fx-rates.py` |
| Local transformations/analysis | `run-local-pipeline.py`, `run-local-analysis.py`, `evaluate-local-risk.py` |
| Model comparison and dashboard exports | `compare-local-model.py`, `build-model-dashboard.py`, `export-verified-batch.py`, `build-dashboard.py` |
| Glue/Spark parity checks | `test-glue-local.ps1`, `test-cloud-scoring-local.ps1`, `verify-aws-full.py`, `verify-cloud-scoring-full.py` |
| Optional streaming demonstration | `prepare-streaming-demo.py`, `replay-streaming-demo.py`, `verify-streaming-demo.py` |
| Diagram sources and PNG exports | `build-architecture-diagrams.py`, `export-architecture.ps1` |

Other `prepare-*`, `verify-*`, policy and evidence helpers support the separate
milestones. Use the [documentation index](../planning/README.md) to find their
prerequisites. Preparation/verification helpers are distinct from paid deployment,
ingestion and replay commands. Generated artifacts belong under ignored `output/`.
