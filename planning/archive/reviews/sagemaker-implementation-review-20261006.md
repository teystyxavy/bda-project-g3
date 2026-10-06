# SageMaker implementation review — 6 October 2026

**Status: implemented and locally verified; cloud deployment and training pending.**
This adds an on-demand AWS training path for the existing BQ1 XGBoost comparison.
The one-command Kaggle/FX-to-dashboard ingestion workflow remains a proposal.

## Prepared deployment and first run

The saved, read-only Terraform plan creates **3 resources, changes 0, destroys 0**:

1. `is459-g3-training` immutable ECR repository with encryption and image scanning.
2. `is459-g3-sagemaker-training` execution role, trusted only by Group 3 SageMaker
   training jobs in Singapore with source-account/source-ARN conditions.
3. Scoped role policy: training-prefix S3 reads/writes, repository image pulls,
   training logs and SageMaker metric namespaces. ECR authentication and metric
   publication require wildcard resources because those APIs have no resource ARN.

State, provider data and saved plan are under
`%LOCALAPPDATA%/IS459-G3/terraform-sagemaker/`, outside OneDrive.
No apply, image push, training execution, endpoint, model promotion or dashboard
publication has occurred.

The prepared request is `output/sagemaker-full-prepared-20261006/request.json`.
Its fixed scope is:

- Account `588738581042`, region `ap-southeast-1`.
- Job `is459-g3-train-full-20261006`.
- Verified baseline input: **5,078,345 rows**, matching the full-export source hash.
- One `ml.m5.xlarge`, 20 GB volume, four CPU threads, at most 120 rounds per candidate.
- Two preregistered depth candidates; validation-only selection, then test/tail reports.
- Network isolation, 1,200-second training runtime limit, no Spot or compute retry.
- Output remains candidate-only and private. Existing production model is unchanged.
- Intended image manifest digest:
  `sha256:f34f334d006b934f0af853fab3f7acf0c188373944a6db7ef48972188d55f513`.
  This image currently exists locally; its presence and digest in ECR must be
  verified after an approved push before starting the job.

The current on-demand training quota is **20 instances** for `ml.m5.xlarge`, quota
code `L-CCE2AFA6`; this is a quota ceiling, not a reservation of available capacity.
Read-only AWS Pricing lookup for `AmazonSageMaker`, usage
`APS1-Train:ml.m5.xlarge` returned **USD 0.288 per instance-hour**. Twenty billable
minutes at that rate would be USD 0.096 in training-instance charges. This is an
illustration, not a total-cost cap: provisioning/data transfer time, billable-time
rules, storage, ECR, logs and taxes must be considered separately. The existing
AWS Budget alert does not prevent spending.

## Validation evidence

- The trainer and launcher tests cover source/request tampering, chronological
  freeze and reload, failed duplicate claims, exact submission, archive safety,
  artifact collection/provenance, and the installed AWS API request schema.
- Terraform `validate` and one mocked-provider test passed. The real deployment
  plan is read-only and has exactly the three new resources described above.
- The actual Linux AMD64 container built with Python 3.12 and XGBoost CPU 3.4.1.
  Its base image is pinned and it requires no runtime package downloads.
- A **320-row synthetic local container smoke run**, using the real SageMaker
  `/opt/ml` channel/config/output layout and `--network none`, completed training,
  validation selection and test/tail reports. Native model artifacts reload on
  the host. Its validation gate failed as expected for this deliberately simple
  fixture; the candidate was not promoted. Smoke metrics are not dataset findings.
- Full-population source hashes and row count were verified during preparation
  and verified again from the prepared bundle. The full population has not been
  trained in this new container yet.

Machine-readable hashes and check results are in
`../../evidence/sagemaker-implementation-validation-20261006.json`.

## Operational boundary

The normal ingest proposal should score with the approved frozen model. Training
must be an explicit mode, producing a candidate rather than silently replacing
the model on every refresh. The SageMaker trainer currently consumes the verified
baseline export; wiring its cloud production into the ingest workflow is separate
work. A future Step Functions branch can reuse this bounded training request.

After cloud completion, collect and verify artifacts; compare validation and main
test ranking results against rules and the existing model. A passed gate alone
does not authorize promotion. Verify portable inference against Glue/Lambda before
any separately reviewed scoring/dashboard release. The sparse tail remains
separate from the representative main test period.

## Next approved action

Apply the saved three-resource plan; upload the reviewed container to the new
repository; verify its scan and digest; stage the prepared inputs; execute one
bounded full-data training job; collect and verify its private artifacts and report
its metrics and billable time. Do not rerun paid compute automatically on failure.
No endpoint, streaming replay, production model replacement or dashboard
publication is included in this action.

The workspace `AGENTS.md` requires explicit authorization for deployment and paid
resources. This review makes that next action concrete; no additional implementation
approval is needed for the local source and tests already completed.

## Sources

- [SageMaker training-image contract](https://docs.aws.amazon.com/sagemaker/latest/dg/your-algorithms-training-algo-dockerfile.html)
- [Training request and runtime controls](https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_CreateTrainingJob.html)
- [Scoped execution-role permissions](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-roles.html)
- [Confused deputy protection](https://docs.aws.amazon.com/sagemaker/latest/dg/security-confused-deputy-prevention.html)
- [AWS pricing](https://aws.amazon.com/sagemaker-ai/pricing/); exact price verified
  with the read-only AWS Price List API on 6 October 2026.
