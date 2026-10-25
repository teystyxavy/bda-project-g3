# Dashboard ingestion controls

The live dashboard uses the existing Group 3 AWS identity through a local sign-in helper. No email invitation, Cognito account or dashboard password is required. This API reuses the existing acquisition and ingestion workflows.

## Operator sign-in

From the repository root with the project Python environment activated:

```powershell
python scripts/aws/dashboard-signin.py
```

If AWS login has expired, first run `aws login --profile is459-g3`, complete AWS sign-in and rerun the helper. The existing tools profile obtains credentials from that login session. Never enter console passwords or access keys into the dashboard.

The helper verifies the exact identity `arn:aws:iam::588738581042:user/g1team3`, signs the bootstrap request with AWS SigV4 and opens the live dashboard. Other AWS identities cannot obtain operator sessions. AWS credentials never enter the browser.

## Configuration and progress

Choose HI-Small, a 1,000-row demo, a custom limit from 1 to 5,100,000 rows, or the full dataset. Options also include a pinned Kaggle version, source refresh, rebuilding stages and candidate training on full runs. Preview displays the equivalent cloud ingestion command without starting a run. Accounts and historical FX remain mandatory inputs. Other subsets and individual-file selection remain unsupported by the cloud pipeline. Download directories, offline FX preparation, administrative unlock and EventBridge setup remain script operations.

The dashboard polls real parent/child workflow history every ten seconds, pauses when hidden and backs off on errors. Its AWS diagram highlights active services and distinguishes skipped/reused stages. It does not invent byte counters, completion percentages or finish estimates. Failed ingestion keeps the last successful business results available. Public read-only status remains available without signing in.

## Session and launch protection

- Bootstrap requires API Gateway AWS IAM authentication and an exact operator ARN check in Lambda.
- Random handoffs expire after two minutes and are consumed atomically once. The browser removes the URL fragment before exchange. The helper never prints the sign-in URL.
- Random dashboard sessions last one hour and remain in tab session storage. Every request checks the session; sign-out revokes it immediately. DynamoDB stores only token hashes. Explicit expiry checks enforce expiration; TTL provides housekeeping.
- Logs contain request metadata, never authorization headers, handoffs, sessions or workflow payloads.
- Stable request IDs make retries return the same execution. A conditional launch reservation plus existing workflow and lock checks prevent overlapping ingestion. No dashboard unlock, cancel, automatic paid-job retry or model promotion is provided.
- Operator history shows that operator's submissions. Workflow history is read without payloads; responses expose only reviewed status, options and same-origin result links.

## Local validation

```powershell
python scripts/aws/prepare-dashboard-api.py --output output/dashboard-control-package
python -m pytest tests/test_dashboard_signin.py tests/test_dashboard_sessions.py tests/test_dashboard_api.py tests/test_dashboard_status.py -q
node --test tests/dashboard-controls.test.cjs
terraform -chdir=infra/dashboard-control init -backend=false
terraform -chdir=infra/dashboard-control validate
terraform -chdir=infra/dashboard-control test
```

The Lambda zip contains dashboard_api.py and dashboard_sessions.py. Python 3.12 supplies boto3. No datasets, model libraries or credentials enter the package.

## Deployment

Deployment requires explicit authorization. Keep state and saved plans in the project's private local AWS directory, outside the repository. Supply the absolute prepared zip path as lambda_zip_path. Review and apply the saved plan. Initial deployment created 18 resources with zero changes and zero deletions; it created no AWS users or Cognito resources.

1. Apply the reviewed control stack plan, preserving existing data pipeline stacks.
2. Export public_configuration to UTF-8 JSON. Upload it to the existing hosting bucket at dashboard/ingestion-config.json with application/json, no-store,max-age=0 and AES256 encryption. Invalidate /ingestion-config.json on the existing CloudFront distribution.
3. Publish the reviewed dashboard assets using the existing release procedure. Retain prior immutable releases and verified business results.
4. Update the processing image's bundled dashboard assets and pin its digest using the guarded ingestion update procedure. Otherwise a later pipeline publication would restore the older UI.
5. Check unsigned rejection, exact AWS identity sign-in, one-use handoffs, sign-out revocation, idempotent submission, concurrent launch refusal, real workflow stages, a bounded smoke run and verified reuse. Keep evidence locally without session tokens. Restore the full business snapshot after smoke publication when appropriate.

Public configuration contains identifiers, not authorization:

```json
{
  "schema_version": 1,
  "api_url": "https://a6xganspgk.execute-api.ap-southeast-1.amazonaws.com",
  "auth_mode": "aws-iam-helper",
  "client_id": "is459-g3-aws-operator",
  "redirect_uri": "https://d1n61tlw9pcrog.cloudfront.net/"
}
```

The API uses bounded Lambda concurrency, API throttling, on-demand DynamoDB, 14-day logs and the existing SNS alarm destination. Charges depend on usage; batch compute remains the main run cost. Include this stack and public configuration in final authorized cleanup.

AWS references: [HTTP API IAM authorization](https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-access-control-iam.html), [execution history](https://docs.aws.amazon.com/step-functions/latest/apireference/API_GetExecutionHistory.html).

## Verification recorded on 7 October 2026

The control stack and updated worker are deployed. Exact AWS identity sign-in, unsigned rejection, one-use and expired handoffs, browser preflight and sign-out revocation passed against the live service. A 1,000-row version-8 run completed all required stages in 515 seconds. A second submission reused that verified release in 10 seconds. Idempotent retry and concurrent-launch rejection passed. The full business snapshot was then restored with the new controls.

Local validation: 215 non-Spark Python tests passed (one skipped), three additional helper tests passed, 15 dashboard interaction tests passed, and Terraform validation plus one mocked infrastructure test passed. CloudFront asset hashes matched the reviewed release. Visual browser acceptance remains unavailable because the browser tool could not initialize. See [deployment evidence](../../planning/evidence/dashboard-live-controls-20261007.json).
