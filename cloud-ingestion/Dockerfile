FROM public.ecr.aws/lambda/python:3.12@sha256:6a347f1d5023ddb33291484cc5734b7198f9087846c30166909a9cae30155f09
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN python -m pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt
COPY bda_pipeline ${LAMBDA_TASK_ROOT}/bda_pipeline
ENV PYTHONDONTWRITEBYTECODE=1 KAGGLEHUB_VERBOSITY=warning
CMD ["bda_pipeline.cloud_acquisition.handler"]
