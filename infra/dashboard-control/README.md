# Dashboard ingestion controls

**Implemented locally; not deployed or cloud-verified.** This stack adds an
invitation-only Cognito user pool, operator group, HTTP API with JWT authorization,
a small Python Lambda and an on-demand DynamoDB launch reservation. It reuses the
existing `is459-g3-cloud-ingest` and `is459-g3-ingest` workflows. It does not provision
another data pipeline, change model contracts or start any ingestion automatically.

The dashboard's Ingestion tab chooses HI-Small, a 1,000-row smoke or full run,
an optional Kaggle version, refresh/rebuild switches and full-run candidate training.
Other subsets are visibly disabled. Historical FX and accounts remain mandatory
pipeline inputs; selecting individual files would violate the current cloud contract.

## Local preparation and validation

From the repository root with the documented Python environment and `[dev,cloud]`
dependencies installed:

```powershell
python scripts/aws/prepare-dashboard-api.py --output output/dashboard-control-package
python -m pytest tests/test_dashboard_api.py tests/test_release.py -q
node --test tests/dashboard-controls.test.cjs
```

The zip contains only `dashboard_api.py`. Lambda's Python 3.12 runtime supplies
boto3/botocore; no datasets, credentials or model libraries enter the artifact.
Terraform validation and mock tests require the AWS provider installed locally:

```powershell
terraform -chdir=infra/dashboard-control init -backend=false
terraform -chdir=infra/dashboard-control validate
terraform -chdir=infra/dashboard-control test
```

## Deployment and operator setup

Deployment creates cloud resources and requires explicit user authorization. Keep
state and real variables under the project's private local AWS directory, using the
same backend workflow as the existing stack helpers. Supply `lambda_zip_path` as
the absolute path to the prepared zip. `dashboard_url` defaults to the existing
CloudFront root. Change `cognito_domain_prefix` if its global name is unavailable.
Review a saved plan before applying. No deployment helper is added here.

After an authorized deployment:

1. In the Cognito console, invite each team member to the new user pool and add them
   to `ingestion-operators`. The member completes first sign-in themselves. Never
   put passwords, users' tokens or invitations in Terraform variables or the repo.
2. Export the `public_configuration` Terraform output to an UTF-8 JSON file and
   publish it to the **existing hosting bucket**, key `dashboard/ingestion-config.json`,
   content type `application/json`, cache control `no-store,max-age=0`. Invalidate
   `/ingestion-config.json` in the existing CloudFront distribution. This public file
   contains only endpoint/client identifiers; it grants no access by itself.
3. Publish the reviewed `index.html`, `dashboard.js` and `dashboard.css` to the
   existing root dashboard keys, and invalidate `/`, `/index.html`, `/dashboard.js`
   and `/dashboard.css`. Do not rewrite historical immutable release assets.
4. Update the **ingestion processing image's bundled dashboard assets** using the
   existing preparation/build/update workflow before the next pipeline run. The
   publisher exports its bundled assets to the root on each new release; an older
   image would otherwise overwrite this UI. See the ingestion runbook.
5. Cloud-check unauthenticated rejection, operator-group enforcement, sign-in/out,
   submission retries, concurrent launch refusal, live parent/child stages and a
   successful 1,000-row run. Then check a verified reuse run. No such cloud tests
   have been performed by this implementation task.

Configuration shape (use the actual deployment output):

```json
{
  "schema_version": 1,
  "api_url": "https://example.execute-api.ap-southeast-1.amazonaws.com",
  "cognito_domain": "https://example.auth.ap-southeast-1.amazoncognito.com",
  "client_id": "replacewithpublicclientid",
  "redirect_uri": "https://d1n61tlw9pcrog.cloudfront.net/"
}
```

Config lives at the hosting root, separately from the four immutable release assets,
so a reused historical snapshot still loads in the new shell. Ingestion works even
when no results snapshot exists yet. Without configuration, the tab explains setup
is pending and disables launch controls; it never simulates progress.

## Progress, ownership and cost controls

- Browser uses authorization code + PKCE and a random, single-use state checked
  against session storage. Access tokens remain in tab session storage for up to
  60 minutes; refresh tokens are discarded. Sign out clears the local session and
  opens Cognito logout. Like ordinary JWTs, already-issued access tokens can remain
  valid until expiry. No AWS/Kaggle credentials are sent to the browser.
- API Gateway verifies issuer, audience, signature, expiry and `ingestion/control`
  scope. The handler additionally requires `token_use=access`, an exact operator
  group and a hashed subject prefix on every inspected execution. Users see their
  own latest 20 runs among the latest 500 submissions, not CLI/other users' history.
- Every submission has a stable request ID. Retrying a lost response returns the
  same execution, even after completion; changing its options returns a conflict.
  A conditional DynamoDB reservation serializes UI launches before workflow history
  becomes visible. A terminal workflow permits a new reservation. A failed start
  without an execution permits reclaim after five minutes. No expiration alone
  permits replacing a known running execution.
- Existing command-driven runs are checked before launch. The current batch lock
  remains authoritative for concurrent CLI starts and failed-worker recovery.
  UI launches have no cancel, unlock, automatic paid-job retry or model-promotion
  action. A failed batch can still require the existing runbook's lock recovery.
- While visible, the page checks parent and child workflow stages every 10 seconds;
  failures back off to 60 seconds. Polling pauses in hidden tabs and on session expiry.
  No status failure changes the last known stage to complete. Refresh resumes
  tracking with the saved run ID. Parallel, skipped and reused stages are explicit.
- A labelled AWS flow diagram highlights all active phases, including parallel
  branches, with a separate current-service line derived from the entered workflow
  state. It distinguishes Lambda control steps from Glue/SageMaker compute jobs.
  The [editable architecture source](../../planning/architecture/dashboard-ingestion-flow.drawio)
  and companion guide describe the same batch/publication path.
- Whole source acquisition (Kaggle + FX) is one workflow stage. Bytes/row counters,
  exact completion percentage, estimated remaining time and live raw logs are not
  implemented. Execution history is requested **without payloads**; API responses
  expose only reviewed selection/status and same-origin result links.
- Successful publication loads that immutable result's JSON into the existing shell,
  avoiding CloudFront's root pointer delay. The AML/treasury views then show that
  release; failed runs leave the last successful result available. Historical release
  pages remain unchanged. The existing four-asset packaging contract is preserved.

The control plane uses HTTP API, bounded Lambda, on-demand DynamoDB and Cognito's
Lite tier. Its incremental charges depend on usage; this task does not claim a
measured AWS cost. Batch compute remains the larger run cost. Keep the USD 250
budget alert and verify charges during the authorized smoke test. For final cleanup,
include this stack; intentionally disable Cognito deletion protection only during
authorized cleanup, then remove the public config and disable the dashboard buttons.

AWS references: [JWT authorizers](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html),
[Cognito PKCE](https://docs.aws.amazon.com/cognito/latest/developerguide/using-pkce-in-authorization-code.html),
[execution history](https://docs.aws.amazon.com/step-functions/latest/apireference/API_GetExecutionHistory.html).
