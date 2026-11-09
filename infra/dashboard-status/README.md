# Dashboard pipeline status (public, read-only)

**Deployed 7 October 2026:** 10 additions, 0 changes, 0 deletions; post-apply plan
shows no drift. Public endpoint:
<https://i5xro01dni.execute-api.ap-southeast-1.amazonaws.com/status>.
Dashboard assets, endpoint configuration and the bundled processing image are
published. GET returns the latest run plus six recent runs; POST `/status` and
GET `/runs` return 404. The live response passes the dashboard render harness.
Final browser visual acceptance remains unverified because the browser tool
failed to initialize. See [deployment evidence](../../planning/evidence/dashboard-status-exploration-result-20261007.json).

Gives the deployed dashboard's Ingestion tab a
live view of the AWS pipeline without sign-in. One unauthenticated `GET /status` route
on an HTTP API calls a small Lambda that reads Step Functions status for
`is459-g3-cloud-ingest` and its `is459-g3-ingest` child. It returns the 10 most recent
runs and the newest run's stages (the same stage mapping as the control API).

It cannot start, stop or change anything. IAM allows only `states:ListExecutions`,
`states:DescribeExecution`, `states:GetExecutionHistory` and its own logs. History is
read **without payloads**; run options are whitelisted (`subset`, `rows`,
`dataset_version`, `train`); no ARNs or account IDs are returned. The Lambda caches responses for 15 s,
the API is throttled (rate 5, burst 10) and the Lambda has reserved concurrency 2, so
public traffic cannot drive unbounded AWS calls. The dashboard only polls while the
Ingestion tab is open and visible.

If `infra/dashboard-control` is deployed later, signed-in operators keep its controls;
everyone else still sees this read-only view.

## Local preparation and validation

```powershell
python scripts/aws/prepare-dashboard-status.py --output output/dashboard-status-package
python -m pytest tests/test_dashboard_status.py tests/test_dashboard_api.py -q
node --test tests/dashboard-controls.test.cjs
terraform -chdir=infra/dashboard-status init -backend=false
terraform -chdir=infra/dashboard-status validate
terraform -chdir=infra/dashboard-status test
```

## Deployment (requires explicit authorization)

1. Plan and apply this stack with `lambda_zip_path` set to the absolute path of the
   prepared zip, using the [shared S3 backend](../tfstate-bootstrap/README.md).
2. Export the `public_configuration` output to UTF-8 JSON and publish it to the
   existing hosting bucket, key `dashboard/status-config.json`, content type
   `application/json`, cache control `no-store,max-age=0`. It holds only the public
   endpoint URL. Invalidate `/status-config.json`.
3. Publish the updated `index.html`, `dashboard.js` and `dashboard.css` to the root
   dashboard keys and invalidate them, as for the control stack.
4. Update the ingestion processing image's bundled dashboard assets before the next
   pipeline run; the publisher otherwise restores the older UI on each release.
5. Check: open the Ingestion tab, confirm the latest run and recent runs appear, and
   that `POST /status` and other paths return 404.

Without `status-config.json` the tab says status is not connected and shows no
disabled controls. For final cleanup, delete `status-config.json` and destroy this stack.
