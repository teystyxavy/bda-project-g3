# =============================================================================
# streaming/Dockerfile -- container image for the streaming demo Lambda
# =============================================================================
# What it builds: the AWS Lambda function that consumes the Kinesis replay
# stream (Kinesis -> Lambda -> DynamoDB + S3 audit), see infra/streaming.
# It is a container-image Lambda (not a zip) because XGBoost, pandas and
# numpy are too large for a normal zip deployment package.
#
# Build context: NOT this folder directly. Run
#   python scripts/streaming/prepare-streaming-container.py --artifacts <model> --output <dir>
# which assembles <dir> with this Dockerfile, requirements.txt, handler.py,
# the bda_pipeline package and the hash-verified frozen model "artifacts/",
# then build and push the image from <dir> to ECR (separate, approved step).
#
# Base image: the official AWS Lambda Python runtime from the public ECR
# gallery, pinned by sha256 digest so every rebuild uses identical bytes.
FROM public.ecr.aws/lambda/python@sha256:517bcc7dc1a3ba62e324961c1563cc54a2bd6dc1060188f9d376211ecb7b2926
# Install Python dependencies first (own layer, cached between rebuilds).
# --no-cache-dir keeps pip's download cache out of the image.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
# Shared pipeline code (validation, rolling features, model loading).
# ${LAMBDA_TASK_ROOT} is /var/task, where Lambda looks for function code.
COPY bda_pipeline ${LAMBDA_TASK_ROOT}/bda_pipeline
# Frozen model files + model-manifest.json; handler.py loads them from
# /var/task/artifacts and checks them against MANIFEST_SHA256 at startup.
COPY artifacts ${LAMBDA_TASK_ROOT}/artifacts
# The Lambda handler module (AWS adapter around bda_pipeline.streaming).
COPY handler.py ${LAMBDA_TASK_ROOT}/handler.py
# Lambda calls handler.py -> handler(event, context) for each record batch.
CMD ["handler.handler"]
