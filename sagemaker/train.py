"""Entry point for `docker run IMAGE train`, with no runtime package downloads.

Where this sits in the pipeline
-------------------------------
This is the default ENTRYPOINT of the image built from ``sagemaker/Dockerfile``
and is what a SageMaker Training job runs. SageMaker starts the container
with the single argument ``train`` (hence the argument check below), after
copying the S3 input channels into ``/opt/ml/input/data/<channel>/``:

* ``baseline``: the rule-scored full population (``bq1_scored.parquet``) and
  its baseline dashboard snapshot;
* ``manifest``: the reviewed ``input.json`` with expected hashes.

The actual work is ``bda_pipeline.sagemaker_training.train_channels``, which
re-verifies the inputs, trains/compares the XGBoost candidate ranker and
writes:

* ``/opt/ml/model``: model artifacts (SageMaker packs this folder into
  ``model.tar.gz`` in S3);
* ``/opt/ml/output/data``: the comparison report.

Hyperparameters (``rounds``, ``threads``, ``expected_rows``) are read from
``/opt/ml/input/config/hyperparameters.json``; SageMaker stores them as
strings, so they are converted with ``int()``.
"""
import json
import sys
from pathlib import Path

from bda_pipeline.sagemaker_training import train_channels


def main():
    """Run one training job inside the container.

    Side effects: on failure writes up to 1024 characters of the error
    message to ``/opt/ml/output/failure``, which SageMaker shows as the job's
    FailureReason, then re-raises so the job is marked Failed.

    Raises:
        ValueError: if called with any argument other than nothing or
        ``train`` (this image is not meant to serve models).
    """
    if sys.argv[1:] not in ([], ['train']):
        raise ValueError('This image supports training only')
    # Standard SageMaker container directory layout.
    root = Path('/opt/ml')
    try:
        parameters = json.loads((root / 'input/config/hyperparameters.json').read_text())
        train_channels(root / 'input/data/baseline', root / 'input/data/manifest',
                       root / 'output/work/candidate', root / 'model', root / 'output/data',
                       rounds=int(parameters['rounds']), threads=int(parameters['threads']),
                       expected_rows=int(parameters['expected_rows']))
    except Exception as error:
        # Truncated to 1024 chars to keep the failure reason short.
        (root / 'output').mkdir(parents=True, exist_ok=True)
        (root / 'output/failure').write_text(str(error)[:1024], encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
