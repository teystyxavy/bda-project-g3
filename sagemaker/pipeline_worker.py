"""AWS Processing entry point. Data transfer is managed by SageMaker, not this worker.

Where this sits in the pipeline
-------------------------------
The ingestion Step Functions workflow starts two SageMaker Processing jobs per
run, both using the image built from ``sagemaker/Dockerfile``. The request
(``ingest_contract.processing_request``) overrides the image's default
entrypoint with ``python /app/pipeline_worker.py <stage>``:

* ``baseline``: ``ingest_compute.baseline_stage`` re-verifies the curated
  Parquet against the raw CSVs and FX rates, then scores the full population
  with the rule baseline (``batch.export_verified_batch``).
* ``export``: ``ingest_compute.export_stage`` verifies the Glue model scores
  and the Athena corridor result, builds ``dashboard.json`` and prepares the
  release (``release.prepare_release``).

SageMaker copies the S3 inputs to ``/opt/ml/processing/input`` before the
container starts and uploads ``/opt/ml/processing/output`` to
``s3://<bucket>/pipeline/runs/<run_id>/<stage>/`` afterwards. The job runs with
network isolation, so this script never calls AWS itself. Any exception
makes the process exit non-zero, which marks the job (and the workflow
step) as failed.
"""
import sys
from bda_pipeline.ingest_compute import baseline_stage, export_stage


def main():
    """Run the stage named on the command line ('baseline' or 'export').

    Raises:
        ValueError: if the argument is missing or not one of the two stages.
    """
    if len(sys.argv) != 2 or sys.argv[1] not in ['baseline', 'export']:
        raise ValueError('Choose baseline or export')
    runner = baseline_stage if sys.argv[1] == 'baseline' else export_stage
    # Fixed SageMaker Processing paths, plus the dashboard shell baked into
    # the image at /app/dashboard (see sagemaker/Dockerfile).
    runner('/opt/ml/processing/input', '/opt/ml/processing/output', '/app/dashboard')


# Run main() only when executed as a script, not when imported.
if __name__ == '__main__': main()
