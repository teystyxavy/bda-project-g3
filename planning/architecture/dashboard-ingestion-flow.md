# Dashboard ingestion flow and live AWS service map

> **Status, 9 November 2026:** deployed and in use. The tab runs HI-Small and
> HI-Medium (full HI-Medium since 8 Nov). BQ2 now comes from the Glue mule-network
> job, not Athena corridor SQL. Since 9 Nov the tab also offers a **cache-only
> HI-Large** run that ends at `ExistingRelease` without Glue (see
> `output/hi-large-cached-run-20261109/`). The overall architecture is in
> [`docs/is459-g3-aml-pipeline-aws.drawio`](../../docs/is459-g3-aml-pipeline-aws.drawio).

Editable source: [dashboard-ingestion-flow.drawio](dashboard-ingestion-flow.drawio).
The dashboard embeds a compact SVG version in `dashboard/index.html`; all phase
colours and current-service labels update from the protected status API. This
implementation is deployed. The Glue baseline/export migration passed a fresh
1,000-row AWS run on 1 November 2026. See [setup](../../infra/dashboard-control/README.md).

1. Operators select HI-Small or HI-Medium inputs, or the cache-only HI-Large run, in the Ingestion tab. The existing Group 3
   AWS identity signs session bootstrap; revocable operator sessions, a stable
   submission ID and a DynamoDB reservation protect the API launch path.
2. The Step Functions wrapper `is459-g3-cloud-ingest` invokes the acquisition Lambda
   to retrieve Kaggle and historical FX, validate inputs and write encrypted S3 raw
   data. If an identical request already has a published result, the `Cached?` choice
   ends the run at `ExistingRelease` without starting the batch.
3. The batch workflow runs Glue curation and writes date-partitioned S3 Parquet.
   Verified curated/scored caches may avoid new Glue jobs.
4. Parallel branches run Glue frozen-model scoring for BQ1, the Glue mule-network
   job for BQ2, and Glue for independent checks and rule scoring.
   Optional SageMaker candidate training follows baseline verification in that
   branch. No candidate promotion occurs.
5. Glue export reconciles all requested outputs and builds the
   dashboard. Lambda verifies the release, writes the private S3 hosting origin,
   commits the selected pointer and invalidates CloudFront assets.
6. The browser loads the immutable result JSON into the current shell. The live
   map labels complete, active, reused/skipped, failed/stopped and pending phases.

| AWS service | Purpose shown in the flow |
|---|---|
| API Gateway / Lambda API | Authenticated run submission and progress inspection |
| Existing AWS identity / DynamoDB | Signed bootstrap, revocable sessions and conditional launch reservation |
| Step Functions | Existing acquisition/batch orchestration and phase history |
| Lambda acquisition | Kaggle/FX retrieval and input validation |
| S3 raw / curated / scored | Encrypted source inputs and analytics artifacts |
| Glue | Curation, frozen-model inference, mule-network analysis, independent checks, rule scoring and export |
| Glue catalog / Athena | Catalog of curated data; Athena for ad hoc SQL |
| SageMaker Training | Optional candidate training only |
| Lambda publication / S3 / CloudFront | Verified release publication and dashboard delivery |

The current-service line distinguishes brief Lambda control steps from the Glue
and SageMaker jobs. A waiting/status state is attributed to the worker service
being monitored; Step Functions still coordinates the wait. Boxes group pipeline
phases, so storage writes and publication actions are not claimed as individually
measured stages. Source downloads have no byte counter or overall percentage.

The diagram's required branches run concurrently. The optional training dependency
is dashed; export waits for it only when requested. Streaming is a separate
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
reviewed. Cloud sign-in, run submission and live status were later verified in AWS
(1 Nov HI-Small smoke, 8 Nov HI-Medium ingestion).
