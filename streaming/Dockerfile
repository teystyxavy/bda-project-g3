FROM public.ecr.aws/lambda/python@sha256:517bcc7dc1a3ba62e324961c1563cc54a2bd6dc1060188f9d376211ecb7b2926
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY bda_pipeline ${LAMBDA_TASK_ROOT}/bda_pipeline
COPY artifacts ${LAMBDA_TASK_ROOT}/artifacts
COPY handler.py ${LAMBDA_TASK_ROOT}/handler.py
CMD ["handler.handler"]
