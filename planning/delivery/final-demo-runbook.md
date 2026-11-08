# Final demonstration and submission runbook

**Optimisation update, 6 October 2026:** the full batch optimisations are
implemented and deployed. The fresh 5,078,345-row rebuild took **20m55s**, versus
24m59s previously (**16.3% faster**, one observed run per configuration). Both
business views, all native scores and corridor reconciliation passed unchanged.
The stage-cache run with SageMaker candidate training took **18m02s**, reused both
curated/scored stages and started no Glue jobs. An unchanged ingestion returned its
verified release in **33.85s** without starting the batch. Training never promotes
the candidate automatically. The current dashboard selects
`opt-train-cache-20261006-aws1`, Kaggle version 8.
See the [implementation](../runbooks/pipeline-optimisation-implementation.md) and
[measured evidence](../evidence/pipeline-optimisation-result-20261006.json).
The existing `scripts/ingestion/ingest.ps1` entry point runs AWS acquisition through publication;
`-Train` adds candidate training and `-Rerun` bypasses caches.

Prepared 6 October 2026 from verified project results. This guide uses retained
evidence and existing outputs. No new deployment, cloud job, replay or publication
is needed to rehearse it.

[Local readiness validation](../evidence/final-deliverable-readiness-20261006.json) records
the checked release/screenshot hashes, milestone reports and remaining items.
It does not replace a fresh AWS, public-link or billing check.

## Automated ingestion and SageMaker addition

The approved new workflow completed on 6 October 2026: smoke 1,000 rows, then
full HI-Small 5,078,345 rows with SageMaker candidate training. The full workflow
took 28 minutes 33.9 seconds; training used 159 billable seconds. Both releases
passed native score parity, all corridor reconciliation and public file hashes.
Use the [ingestion runbook](../runbooks/ingestion-runbook.md), [automated architecture](../architecture/automated-ingestion.md)
and [deployment result](../evidence/ingestion-deployment-result-20261006.json). The public
[full release](https://d1n61tlw9pcrog.cloudfront.net/releases/full-20261006-aws1/index.html)
includes both views. Existing-data version is honestly reported as unrecorded.

For a separately requested live rerun, `scripts/ingestion/ingest.ps1 -LocalAcquisition` uses the
currently deployed flow through publication; `-Train` adds a private candidate. An
unchanged successful manifest returns its cached release. Use retained AWS evidence
to rehearse without starting additional paid work. Training never auto-promotes.

The new default launcher targets deployed AWS-side acquisition in Lambda. Its
1,000-row smoke and full 5,078,345-row run passed all reconciliation/public hashes.
Full acquisition took about 53 seconds and the complete flow about 25 minutes.
That earlier acquisition release selected `cloud-full-20261006-aws1`, with recorded Kaggle
version 8. Use [current cloud acquisition evidence](../evidence/cloud-ingestion-deployment-result-20261006.json)
for the final status, separately from the previous completed training run.

## Course requirements

The instructor's `[IS459] Project_G1.pptx` is authoritative. Slides 7 and 9
require final slides and artefacts before the usual **Week 13 lesson time**, with
the presentation in **Week 14**. Confirm the calendar date from the course
schedule. The presentation slot is **15 minutes presenting/demo plus 5 minutes
Q&A**. Every member must attend and present at least one component. Include an
individual-contribution slide describing actual work.

Provide narrated or annotated video evidence showing both setup and running the
pipeline end to end. Upload the videos to YouTube and put their links in the
slide appendix. Submit source code/related files to eLearn or include their
links/info in that appendix. After the final presentation, delete project AWS
resources through a separately approved cleanup plan.

## Fifteen-minute presentation sequence

| Time | Segment | What to show and say |
|---|---|---|
| 00:00–01:00 | Business decisions | Compliance chooses transactions to investigate. Treasury monitors bank/currency volume concentration. Identify the audience and action for each. |
| 01:00–02:00 | Data and architecture | 5,078,345 synthetic transactions; Kaggle source plus cited historical FX. Show the implemented batch backbone and the separate streaming path. |
| 02:00–04:00 | Ingestion and curation | Narrated setup/run excerpt: existing raw inputs, positional account-header renaming, composite account joins, prior-window features and date-partitioned Parquet. Show full-row verification. |
| 04:00–07:00 | BQ1 dashboard | Select main test period and model, show the top-100 queue, open one explanation, then compare rules. Explain 44 labelled positives per 100 model reviews versus zero for rules. |
| 07:00–09:00 | BQ2 dashboard | Show all-period bank/currency corridors, then September 6 versus September 8. Explain the top-five volume share and daily concentration change. |
| 09:00–11:00 | Orchestration | Show the full successful ingestion execution, SageMaker training, and score/Athena reconciliation. Explain freshness checks, bounded compute and failure handling. |
| 11:00–12:30 | Streaming | Show the completed 884-event result, 32 sender states and exact batch-reference parity. Show Disabled consumer status. Use an edited evidence clip because the actual replay took 17 minutes 29.5 seconds. |
| 12:30–14:00 | Cost and limitations | Separate estimates from invoices. Mention retained shard cost, synthetic evaluation, sparse tail, static dashboard, and unresolved image scan findings. |
| 14:00–15:00 | Contributions and artefacts | Every member's actual contribution, recording/source links, cleanup responsibility and the resulting decisions the two views support. |

Assign speakers after the team supplies actual contributions. These segment roles
are suggestions, not a record of who built the components. Rehearse transitions
and count clip playback inside the 15-minute total.

## Evidence to open before recording

| Segment | Existing source | Verified fact |
|---|---|---|
| Full curation | [AWS deployment status](../aws-deployment-status.md), `output/full-cloud-optimized-20261005/parity.json` | 5,078,345 rows and 5,177 positive labels reconciled; valid optimized output |
| Main model comparison | [Local model validation](../archive/reviews/local-model-validation.md), [full cloud result](../evidence/full-cloud-scoring-result-20261006.json) | Chronological selection; test precision@100 44%, recall@100 4.6025%, average precision 0.135546 |
| Published dashboard | [Current ingestion result](../evidence/ingestion-deployment-result-20261006.json) and [full release](https://d1n61tlw9pcrog.cloudfront.net/releases/full-20261006-aws1/index.html) | Full rows; immutable hashes and root pointer verified; both business views published |
| Automated pipeline | [Ingestion result](../evidence/ingestion-deployment-result-20261006.json), `output/ingestion-full-aws-20261006/` | Full workflow succeeded in 28m34s; 5,078,345 scores and 1,082,185 daily bank/currency rows reconciled |
| SageMaker training | Candidate comparison/selection in `output/ingestion-full-aws-20261006/` | Completed in 159 billable seconds; validation precision@100 46% versus rule 2%; private candidate, no promotion |
| Streaming | [Completed replay](../archive/reviews/streaming-demo-result-20261006.md), [result metadata](../evidence/streaming-deployment-result-20261006.json) | 884 results, 32 exact sender histories, zero failures, Disabled consumer |
| Images | `output/cloud-dashboard-release-20261006/published-dashboard.png` and `published-treasury.png` | Previously captured dashboard evidence; do not describe these as a fresh live check |

Recorded deployment/result files are historical evidence. Their statuses describe
their own milestone: the full scoring report predates dashboard publication, and
the publication report predates orchestration/streaming. Use the later results
for current completion claims. None of this evidence establishes today's bill.

## BQ1 walkthrough narration

“Our compliance view ranks transactions for a fixed review capacity. The model
uses transaction signals and strictly preceding sender behaviour. We trained on
September 1–6, selected the model on September 7–8, and evaluated once on
September 9–10. In this synthetic test period, the model's top 100 contains 44
positive labels, compared with zero for our documented rules. That is 44% precision
at 100 and about 4.60% recall. It leaves 912 of the 956 positives outside the queue.
The score is a ranking signal. These explanations describe signed TreeSHAP
contributions to the model margin, and investigators still need to review evidence.”

Show the test period first. Keep labels out of investigator queue rows. The
September 11–18 tail has only 1,108 rows and unusually high prevalence, so present
it as a separate limitation. Explain that the active model was originally selected and frozen locally. The new
SageMaker training job now reproduces the comparison as a private candidate; it
completed in 159 billable seconds and passed the validation gate. AWS Processing
recalculates candidate metrics and performs explanations/export. The candidate was
not promoted, so dashboard metrics still describe the active frozen model.

## BQ2 walkthrough narration

“Treasury needs comparable volume across currencies. Historical FX produces
same-day USD equivalents for this view. Across all 5,078,345 transactions, total
USD-equivalent volume is about USD 1.748 trillion. The five largest directed bank
corridors account for 6.18% of volume and the bank-pair HHI is about 0.001715.
On September 6 the daily top-five share is about 39.41%, compared with 8.57% on
September 8. This dataset therefore has material daily variation that an
all-period total hides. Treasury can review liquidity allocation and corridor
dependence using these views. High volume alone provides no AML suspiciousness
conclusion.”

These metrics refer to directed **bank pairs** in the dashboard. In the new ingestion run, the 1,082,185 Athena result rows contain two
separate grains: daily directed bank pairs and daily directed payment/receiving
currency pairs. Keep the two counts and concentration definitions separate. Cite
Frankfurter for covered fiat rates and Yahoo Finance daily history for Bitcoin,
Ruble and Saudi Riyal, retaining observation dates and source metadata.

## Video capture plan

| Required video | Capture sequence | Completion status |
|---|---|---|
| Setup end to end | Workspace/setup prerequisites, local login with credentials off-screen, existing dataset/FX preparation, separate Terraform stacks and plan review, scoped roles, artifact hashes, private storage, hosting configuration | Recording and YouTube link pending |
| Run end to end | Narrated ingestion/curation evidence, scoring and treasury execution evidence, reconciliation, dashboard output and an orchestration/streaming excerpt | Recording and YouTube link pending |

Use [the automated ingestion runbook](../runbooks/ingestion-runbook.md),
[the batch runbook](../runbooks/aws-batch-runbook.md),
[scoring preparation](../archive/reviews/cloud-scoring-preparation.md),
[hosting review](../archive/reviews/dashboard-hosting-review.md),
[orchestration review](../archive/reviews/orchestration-review-20261006.md) and
[streaming instructions](../../streaming/README.md) as component references.
Show actual operations/evidence and mark time cuts clearly. Reconstructing a
screen walkthrough is preparation; a completed recording must actually show the
setup and run steps with narration or annotations. A screenshot collection alone
does not satisfy those two video requirements. A video plan is not a recorded video.

If a fresh live cloud execution is needed for footage, first prepare a concrete
fresh-prefix/run-ID plan, expected costs and exact scope for approval. Do not reuse
the 884-event receipt or turn on the existing consumer. Login readiness alone
does not authorize another paid run. Do not record passwords, tokens, private
variables, state, browser login forms or alert email addresses. Show useful console
resource/result pages after sign-in and review footage before upload.

Keep an offline copy of clips and the existing dashboard release for rehearsal.
The local snapshot contains `site/index.html`, CSS, JavaScript and JSON together;
if a browser requires HTTP to fetch JSON, use the existing Python environment:

```powershell
output/ml-venv/Scripts/python.exe -m http.server 4174 --bind 127.0.0.1 --directory output/cloud-dashboard-release-20261006/site
```

Open `http://127.0.0.1:4174/`. Stop the server after rehearsal. This serves the
already-built static files locally and starts no AWS work.

## Submission and cleanup checklist

- Final PPTX/PDF with implemented architecture, both business answers and limitations.
- Actual individual contributions, with a speaking/demo role for every member.
- Narrated/annotated setup video and run video, YouTube links in the appendix.
- Source repository/ZIP and component runbooks, without datasets, credentials,
  private state/plans or generated private keys. Review the package before sharing.
- Editable final architecture diagrams: the updated core batch and additional
  enhancement files have four locally rendered, visually reviewed pages.
  Use current `planning/*.drawio` and `planning/architecture/exports/*.drawio.png`;
  the original proposal sources are preserved in their archive.
- Working artifact/video links, checked from the audience's access context.
- Current account spend snapshot near submission, with timestamp and total-course
  tracking separate from the USD 10 monthly notification threshold.
- Separately approved post-presentation cleanup of every project stack, bucket
  version, ECR image and retained stream, followed by absence/cost verification.

Final deck, recordings/links, actual contributions and
post-presentation cleanup remain outstanding. This guide completes rehearsal
preparation and the evidence map.
