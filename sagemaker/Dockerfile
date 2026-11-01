# Training-only image; baseline verification and dashboard export run in Glue.
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
# Training entry point.
COPY train.py /app/train.py
# Default command = training. "docker run IMAGE train" also works, because
# SageMaker Training passes the word "train" as the only argument.
ENTRYPOINT ["python", "/app/train.py"]
