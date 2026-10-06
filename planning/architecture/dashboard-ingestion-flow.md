# Dashboard ingestion flow and live AWS service map

Editable source: [dashboard-ingestion-flow.drawio](dashboard-ingestion-flow.drawio).
The dashboard embeds a compact SVG version in `dashboard/index.html`; all phase
colours and current-service labels update from the protected status API. This
implementation is local. The new UI/API/sign-in have not been deployed or tested
with a new AWS run. See [setup](../../infra/dashboard-control/README.md).

1. Invited operators select HI-Small demo/full inputs in the Ingestion tab. JWT
   authorization, the operator group, a stable submission ID and a DynamoDB
   reservation protect the API Gateway/Lambda launch path.
2. The existing Step Functions acquisition wrapper invokes Lambda to retrieve
   Kaggle and historical FX, validate inputs and write encrypted S3 raw data.
3. The batch workflow runs Glue curation and writes date-partitioned S3 Parquet.
   Verified curated/scored caches may avoid new Glue jobs.
4. Parallel branches run Glue frozen-model scoring for BQ1, Athena corridor SQL
   through the Glue catalog for BQ2, and SageMaker Processing for the rule baseline.
   Optional SageMaker candidate training follows baseline verification in that
   branch. No candidate promotion occurs.
5. SageMaker export Processing reconciles all requested outputs and builds the
   dashboard. Lambda verifies the release, writes the private S3 hosting origin,
   commits the selected pointer and invalidates CloudFront assets.
6. The browser loads the immutable result JSON into the current shell. The live
   map labels complete, active, reused/skipped, failed/stopped and pending phases.

| AWS service | Purpose shown in the flow |
|---|---|
| API Gateway / Lambda API | Authenticated run submission and progress inspection |
| Cognito / DynamoDB | Invited operator identity and conditional launch reservation |
| Step Functions | Existing acquisition/batch orchestration and phase history |
| Lambda acquisition | Kaggle/FX retrieval and input validation |
| S3 raw / curated / scored | Encrypted source inputs and analytics artifacts |
| Glue | PySpark curation and frozen-model inference |
| Glue catalog / Athena | Metadata-mediated treasury queries over S3 |
| SageMaker Processing / Training | Baseline verification, optional candidate and export |
| Lambda publication / S3 / CloudFront | Verified release publication and dashboard delivery |

The current-service line distinguishes brief Lambda control steps from the Glue
and SageMaker jobs. A waiting/status state is attributed to the worker service
being monitored; Step Functions still coordinates the wait. Boxes group pipeline
phases, so storage writes and publication actions are not claimed as individually
measured stages. Source downloads have no byte counter or overall percentage.

The diagram's required branches run concurrently. The optional training dependency
is dashed; export waits for it only when requested. Streaming is a separate optional
demonstration and is outside this batch-run diagram.

Native diagrams use verified AWS4 service stencils. SageMaker, Glue and Athena
were checked in the trusted local `aws4-reference.xml` from draw.io 31.7.0; the
remaining service names are documented in the project-local diagram skill. The
dashboard uses text-labelled phase cards for readability. No new standalone runtime
asset is introduced, so existing four-asset release verification still applies.

Regenerate locally with `python scripts/architecture/build-dashboard-flow.py`.
PNG exports retain embedded XML and are stored in `planning/architecture/exports/`.

Local validation on 6 October 2026: 190 Python tests passed (one skipped), nine
offline browser-logic tests passed, and Terraform validation plus its provider-mock
test passed. The updated four-asset release and Lambda zip were prepared without
AWS calls. Desktop and 390-pixel layouts and both native PNG pages were visually
reviewed. Cloud sign-in, run submission and service status remain unverified until
an explicitly authorized deployment and ingestion smoke run.
