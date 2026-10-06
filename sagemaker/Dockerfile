FROM python:3.12-slim-bookworm@sha256:54c85f3c47607a77f32adec749d3c81d1348bf25833671f512b26a9b6d778cb3
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY bda_pipeline /app/bda_pipeline
COPY train.py /app/train.py
COPY pipeline_worker.py /app/pipeline_worker.py
COPY dashboard /app/dashboard
ENTRYPOINT ["python", "/app/train.py"]
