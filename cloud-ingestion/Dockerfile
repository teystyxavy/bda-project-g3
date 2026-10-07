# =============================================================================
# cloud-ingestion/Dockerfile -- container image for the acquisition Lambda
# =============================================================================
# What it builds: the AWS Lambda function is459-g3-acquire (Terraform in
# infra/cloud-ingestion). It is the first step of the pipeline: it downloads
# the Kaggle IBM AML "HI-Small" transactions/accounts files and the FX rates,
# checks them and writes them to the S3 raw area for the Glue jobs
# (code: bda_pipeline/cloud_acquisition.py -> handler).
# A container image is used because the dependencies are larger than a zip
# Lambda package allows.
#
# Build context: assembled by scripts/aws/prepare-cloud-ingestion.py, which
# copies only this Dockerfile, requirements.txt and a cut-down bda_pipeline
# package (acquisition modules only) and records their SHA-256 hashes;
# scripts/aws/deploy-cloud-ingestion.ps1 re-checks them before deploying.
#
# Base image: official AWS Lambda Python 3.12 runtime, pinned by sha256 digest.
FROM public.ecr.aws/lambda/python:3.12@sha256:6a347f1d5023ddb33291484cc5734b7198f9087846c30166909a9cae30155f09
# Install dependencies into the Lambda task root (/var/task).
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN python -m pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt
# The acquisition subset of the pipeline package.
COPY bda_pipeline ${LAMBDA_TASK_ROOT}/bda_pipeline
# No .pyc files; keep kagglehub quiet so logs only show warnings/errors.
ENV PYTHONDONTWRITEBYTECODE=1 KAGGLEHUB_VERBOSITY=warning
# Lambda invokes bda_pipeline/cloud_acquisition.py -> handler(event, context).
CMD ["bda_pipeline.cloud_acquisition.handler"]
