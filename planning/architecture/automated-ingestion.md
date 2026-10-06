# Automated source-to-dashboard architecture

The current full-batch workflow additionally runs baseline verification, frozen
scoring and Athena together after curation, with verified stage reuse and bounded
SageMaker completion checks. See the
[current optimisation flow](../runbooks/pipeline-optimisation-implementation.md). The editable
diagram described below is retained as the local-acquisition predecessor.

This diagram records the verified local-acquisition predecessor. The new revision
moves Kaggle and FX retrieval to Lambda and adds a wrapper workflow; see
[cloud acquisition flow and deployment review](../archive/reviews/cloud-ingestion-review.md). That
revision is deployed; successful smoke/full acceptance is recorded in
`../evidence/cloud-ingestion-deployment-result-20261006.json`. The local path now requires
`-LocalAcquisition` when launching a run.

The editable source is `automated-ingestion.drawio`; the embedded XML PNG is
`exports/automated-ingestion.drawio.png`. This supplements the older
core and streaming diagrams with the newly deployed command-driven workflow.

1. `ingest.ps1` acquires or reuses Kaggle files, retrieves required historical FX
   rates, verifies a manifest, and uploads separate encrypted raw inputs.
2. Step Functions starts Glue curation, then SageMaker Processing verifies complete
   row identities, account joins, historical FX and causal feature histories.
3. Parallel branches score the frozen model in Glue, query bank/currency corridors
   in Athena, and optionally train a private SageMaker candidate with `-Train`.
4. Export Processing reconciles all scores and corridor metrics, checks candidate
   provenance and recalculates its period metrics, then builds both business views.
5. Lambda verifies the four release assets, uploads immutable versions and commits
   one conditional current pointer. CloudFront serves the selected release.

The SageMaker service stencil `mxgraph.aws4.sagemaker` is verified in the trusted
offline draw.io 31.7.0 library. No guessed service stencil is used. Step Functions dispatches the
parallel branches; the diagram shows representative paths to keep lines readable.

The 33 reviewed resources are deployed; the 1,000-row AWS publication smoke passed.
Full run `full-20261006-aws1` with candidate training passed and published on 6 October 2026. See
[deployment evidence](../evidence/ingestion-deployment-result-20261006.json) for current results.
Training never automatically promotes a model. There is no new recurring schedule.
The existing streaming path remains separate and its consumer remains disabled.
