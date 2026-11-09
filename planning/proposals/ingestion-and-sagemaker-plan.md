# Combined ingestion automation and SageMaker deployment plan

**Later revision:** the launcher now defaults to a dedicated AWS acquisition Lambda
and wrapper workflow. These nine additions are deployed; smoke and full runs passed
reconciliation/publication, recorded in [deployment evidence](../evidence/cloud-ingestion-deployment-result-20261006.json).
See review (archived). This document describes the completed
33-resource predecessor. Use `-LocalAcquisition` for that currently deployed flow.

## Requested outcome

One command acquires the selected Kaggle files and historical FX rates, uploads
verified inputs, and runs the batch pipeline through a refreshed dashboard:

```powershell
.\scripts\ingestion\ingest.ps1 -LocalAcquisition
```

This command is **deployed and verified through a 1,000-row AWS release**.
The full 5,078,345-row run with SageMaker training passed and published.
See the implementation review (archived) for
the exact 33 additions and the [runbook](../runbooks/ingestion-runbook.md) for working commands.
Kaggle and FX acquisition
run locally; after upload and acceptance, AWS completes processing independently
of the terminal. It reports the execution link, stage status and dashboard URL.

```text
Kaggle files + historical FX APIs
               |
       Validate and upload to S3
               |
       Glue curate + validate
               |
       Generate verified rule baseline
               |
       +-------+-----------------------------------+
       |                                           |
       Frozen AML scoring                   Athena corridor analysis
       |                                           |
       +------------------+------------------------+
                          |
            Reconcile + export both dashboard views
                          |
            Validate a versioned dashboard release
                          |
            Publish release pointer via S3/CloudFront

Explicit training mode, after the verified rule baseline:
SageMaker training -> validation selection -> frozen test evaluation
                  -> private candidate model + comparison report
```

Normal ingestion uses the approved frozen model. The implemented `-Train` switch also
produces a SageMaker candidate and its evaluation report; the candidate does not
automatically replace the active model. Model promotion must pass validation,
portable-runtime scoring checks and a reviewed release. Test metrics are reported
after selection and are not used to choose a candidate.

## Implementation changes

| Component | Change | Current status |
|---|---|---|
| Entry point | Add `scripts/ingestion/ingest.ps1` for preflight, acquisition, upload, start, progress and result links | Implemented; API preparation tested |
| Kaggle | Add version/subset options; default `HI-Small`; verify reusable downloads; explicit refresh; record version and hashes | Implemented; version/cache tests pass |
| FX | Discover transaction dates/currencies, retrieve historical rates and prior-day coverage, retain provider/observation dates, enforce missing/stale-rate checks | Implemented; real API preparation and negative tests pass |
| Run manifest | Record source hashes, subset/version, dates, counts, FX provenance, active model hashes, code/image versions and unique run locations | Implemented and tested |
| Glue curation | Invoke the existing transformer with per-run inputs and outputs; check complete IDs, joins, partitions and causal features | AWS smoke and full run passed |
| Rule baseline | Produce the full verified feature/rule export and evaluation manifest in AWS, using the existing baseline logic | AWS smoke processing and baseline verification passed |
| AML scoring | Reuse frozen-model inference with validated per-run arguments instead of hardcoded locations | AWS smoke and full run passed |
| Treasury | Run Athena against that run's curated data and reconcile counts, USD volume and concentration | AWS smoke query and corridor reconciliation passed |
| SageMaker | Train bounded XGBoost candidates and verify versioned model artifacts | Deployed; full training completed in 159 billable seconds; candidate verified |
| Verification/export | Move local reconciliation and dashboard-building logic into cloud execution; export both business views from the same run | AWS smoke export and score/corridor proofs passed |
| Publication | Upload a versioned release; check completeness/schema; update one current-release pointer only after success | AWS smoke release published through the conditional pointer |
| Dashboard | Load the release pointer and display run ID, data period, source version, active model and refresh time | AWS versioned smoke release verified in both views |
| Terraform/workflow | Add ingestion/export/publication resources and a parameterized workflow with scoped permissions | 30 resources deployed; AWS smoke workflow succeeded |

## SageMaker deployment: the three prepared additions

The saved SageMaker Terraform plan is **3 additions, 0 changes, 0 deletions**:

1. **ECR repository:** `is459-g3-training`, encrypted, immutable image tags and scanning.
2. **Execution role:** `is459-g3-sagemaker-training`, trusted only by Group 3
   training jobs in Singapore, with source-account/source-ARN protections.
3. **Scoped inline policy:** training input reads, candidate-output writes,
   image pulls, training logs and SageMaker metrics.

These three resources cover the SageMaker foundation. They **do not cover the
complete ingestion deployment**. The separate ingestion stack added 30 resources
for jobs, baseline/export/publication, permissions and the workflow. Both stacks
were applied on 6 October 2026.
Existing batch, hosting and streaming stacks retain their separate state.

State and the saved SageMaker plan remain outside OneDrive under
`%LOCALAPPDATA%/IS459-G3/terraform-sagemaker/`. See the
SageMaker review (archived) and
[runbook](../../sagemaker/README.md) for the prepared request and deployment commands.

After authorized deployment: push the tested image, verify its ECR digest and
scan, stage the reviewed inputs, run one bounded training job, then collect and
verify the candidate. The prepared full-data request uses 5,078,345 rows, one
`ml.m5.xlarge`, four threads, at most 120 rounds per candidate, network isolation
and a 1,200-second training runtime limit. There is no persistent endpoint,
notebook or Studio domain in this deployment.

The verified training price is USD 0.288 per instance-hour in Singapore. Twenty
billable minutes would be USD 0.096 for the training instance alone, not a total
cost cap. Glue, Athena, S3, ECR, CloudFront, logging and taxes must be accounted
for separately. The course budget remains USD 250; the existing monthly budget
alert is not a hard spending limit.

## Run behaviour and failure handling

- Reuse a successful release for an unchanged processing manifest. Cache identity
  includes source/FX hashes, model, code and configuration versions, not file names
  alone. A changed FX response or model version creates a different processing run.
- An explicit `-Rerun` option creates a fresh execution for demonstrations.
  Training mode must not be silently satisfied by a cached dashboard-only run.
- Use separate raw, curated, scored, query-result and release locations per run.
  Enforce a single active pipeline execution initially to avoid mutable Glue catalog
  locations and competing publication. No success or partial output is overwritten.
- API retrieval uses bounded retries. Paid compute is not blindly repeated; inspect
  a lost acknowledgement using the same execution/job ID.
- Validate source schema and preserve string bank/account identifiers; use composite
  account joins, positional duplicate-column renaming and `txn_date` partitions.
- Missing FX coverage, stale observations, row loss, duplicate IDs, incomplete
  scores, incompatible model artifacts or unreconciled corridor totals stop
  publication. Inspect preserved evidence; the previous release stays available.
- Stage all release objects first and switch one pointer last. Serialize publication
  and account for CloudFront caching; retain the previous pointer for rollback.
- Keep AML suspicion rankings separate from treasury volume/concentration findings.
  Refresh evaluation metrics for the actual dataset rather than reusing old claims.
- Keep credentials outside source, manifests, logs and Terraform state in OneDrive.
  Kaggle credentials remain local for the first implementation.
- Streaming replay, consumer activation and cleanup are outside this batch scope.

## Delivery and deployment sequence

1. Implement and test the local Kaggle/FX acquisition, manifest and entry point.
2. Parameterize cloud curation/scoring/query inputs and implement the cloud rule
   baseline, reconciliation and export jobs.
3. Add versioned publication and dashboard provenance display; test failure paths
   and preservation of the previous release locally.
4. Produce a combined Terraform review with exact additions/updates, permissions,
   estimated run costs and separate state locations. Include the already prepared
   three-resource SageMaker plan in that review.
5. After explicit deployment/run authorization, deploy the reviewed resources and
   verify a small complete AWS ingestion-to-dashboard run before full `HI-Small`.
6. Run the approved full-data path, including SageMaker candidate training when
   explicitly selected; reconcile both business outputs and publish only a passing
   release using the approved model.
7. Update architecture diagrams, setup/demo instructions and evidence with what
   actually ran. Retain a complete cleanup inventory for after the presentation.

## Acceptance criteria

- One ordinary ingest invocation reaches a validated dashboard release without
  separate manual calls to curation, scoring, export or publication.
- AWS processing continues after input upload if the terminal closes; a later
  status check finds the same execution and its result.
- The dashboard proves that BQ1 and BQ2 came from the same input/run and shows
  source, FX and model provenance.
- Identical successful inputs skip paid recomputation; changed processing inputs
  create a new run. Failures retain the previous release and explain the failed stage.
- Explicit training runs in SageMaker and returns verified candidates and ranking
  metrics. Ingestion does not silently change the active model.
- Local tests and a small integration smoke precede a full-data cloud execution.

This document combines the requested scope for review. It does not record approval
to apply Terraform, start paid jobs or publish a new dashboard. The workspace
`AGENTS.md` requires explicit authorization for those actions.

## Technical references

- [KaggleHub versioned downloads](https://github.com/Kaggle/kagglehub)
- [Frankfurter historical rates](https://frankfurter.dev/)
- [Step Functions Glue jobs](https://docs.aws.amazon.com/step-functions/latest/dg/connect-glue.html)
- [Step Functions SageMaker training integration](https://docs.aws.amazon.com/step-functions/latest/dg/connect-sagemaker.html)
- [Locally verified SageMaker implementation](../evidence/sagemaker-implementation-validation-20261006.json)
