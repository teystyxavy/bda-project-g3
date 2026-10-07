# =============================================================================
# sagemaker/Dockerfile -- one image for SageMaker Training and Processing
# =============================================================================
# What it builds: the custom container used by the ingestion workflow for
#   * SageMaker Training: retrains a candidate XGBoost ranker
#     (default ENTRYPOINT below -> train.py -> bda_pipeline.sagemaker_training);
#   * SageMaker Processing: baseline verification/scoring and the final
#     dashboard export/release. The Processing job overrides the entrypoint
#     with ["python", "/app/pipeline_worker.py"] plus "baseline" or "export"
#     (see bda_pipeline/ingest_contract.py).
# SageMaker copies input data from S3 into /opt/ml/... and uploads outputs
# afterwards, so the code inside needs no AWS credentials and the jobs can run
# network-isolated. Nothing is downloaded at runtime: every dependency is
# installed at build time.
#
# Build context: assembled by scripts/aws/prepare-sagemaker-container.py
# (this Dockerfile, requirements.txt, train.py, pipeline_worker.py, the
# bda_pipeline package and the dashboard assets). The image is pushed to ECR
# and referenced by digest.
#
# Base image: slim Debian 12 Python 3.12, pinned by sha256 digest so rebuilds
# are reproducible.
FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
# Unbuffered stdout so progress lines appear in CloudWatch logs promptly;
# no .pyc files written into the image or at runtime.
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# libgomp1 = GNU OpenMP runtime, needed by XGBoost for multi-threading.
# The apt lists are deleted afterwards to keep the image small.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# Dependencies in their own layer so code-only changes rebuild quickly.
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
# Shared pipeline package (training, verification, scoring, release code).
COPY bda_pipeline /app/bda_pipeline
# Entry points: train.py for Training, pipeline_worker.py for Processing.
COPY train.py /app/train.py
COPY pipeline_worker.py /app/pipeline_worker.py
# Static dashboard shell (index.html, dashboard.css, dashboard.js); the
# export stage copies these next to the generated dashboard.json.
COPY dashboard /app/dashboard
# Default command = training. "docker run IMAGE train" also works, because
# SageMaker Training passes the word "train" as the only argument.
ENTRYPOINT ["python", "/app/train.py"]
