# Group 3 architecture and operating reference

Repository evidence as of 6 October 2026. Commands run from `bda-project-g3/`. Replace placeholders. Uncached cloud commands start paid work and publication. This guide is not a fresh health or billing check.

## 1. IBM AML pipeline

Architecture and operating reference

IS459 Group 3

Repository evidence as of 6 October 2026


A detailed learning reference, longer than the course presentation slot. Status describes retained repository acceptance evidence, not a fresh cloud-health or billing inspection.

Source: README.md

## 2. How to use this reference

- **Design:** Business questions, current architecture and service roles
- **Data flow:** Acquisition, curation, features, scoring and verification
- **Operation:** Setup, run commands, dashboard usage and failure recovery
- **Deeper detail:** Sources and explanations appear in slide notes. The companion Markdown contains copyable commands.

Run commands from bda-project-g3/. Replace every placeholder with a valid path or run ID. Cloud commands assume the existing deployed account and compatible artifacts.

Source: README.md; planning/runbooks/ingestion-runbook.md

## 3. Business questions and decisions

- **BQ1: compliance:** Which transactions deserve investigation first? Rank transactions for limited review capacity.
- **Audience and action:** AML analysts review the top 100 and inspect the model evidence.
- **BQ2: treasury:** Which directed bank/currency corridors dominate USD volume, and how does concentration change?
- **Audience and action:** Treasury reviews dependencies, liquidity exposure and correspondent relationships.

BQ1 is a transaction ranking problem. BQ2 is a descriptive exposure analysis. High volume does not imply suspiciousness.

Source: README.md

## 4. Course requirements

- **End to end:** Ingestion through insight visualization, with architecture diagrams supporting both business questions.
- **Cost constraints:** USD 250 course allowance. Avoid Redshift, RDS and QuickSight. AWS Marketplace purchases are prohibited.
- **Submission:** Slides, source and narrated/annotated setup and run videos are due before the usual Week 13 lesson time.
- **Presentation:** Week 14: 15 minutes plus 5 minutes Q&A. Every member presents a component. Cleanup follows.

Confirm the actual calendar dates from the course schedule. Video links belong in the appendix. The instructor brief is authoritative.

Source: ../[IS459] Project_G1.pptx, slides 5–11

## 5. Current implementation status

- **Batch acceptance:** AWS acquisition through Glue, Athena, SageMaker verification/export and CloudFront publication passed full-data checks.
- **Optional training:** SageMaker trains and verifies a private candidate. It does not automatically promote it.
- **Streaming acceptance:** 884 events and 32 sender histories passed comparison. The consumer remains disabled.
- **Future work:** Incremental ingestion, broader streaming optimization, calibrated probabilities and live transaction alerts.

Latest README and optimization evidence supersede early planning descriptions. Dated acceptance does not guarantee permanent availability of retained cloud resources.

Source: README.md; planning/evidence/pipeline-optimisation-result-20261006.json; streaming/README.md

## 6. Important changes since the proposal

- **Model output:** Uncalibrated ranking score. Native UBJ model plus saved schema replaces probability and joblib language.
- **Catalog and FX:** Explicit Glue schema and date partition projection replace crawlers. Yahoo fills FX coverage gaps.
- **Automation:** AWS acquisition and verified publication are implemented. No recurring batch schedule is active. An opt-in EventBridge upload trigger (`ingest.ps1 -LocalAcquisition -Trigger EventBridge`, rule `is459-g3-raw-upload-trigger`, wrapper `is459-g3-raw-trigger`) is deployed as of 6 October 2026 but not yet test-run.
- **Streaming and alerts:** SNS reports processing failures. The demo does not issue high-risk transaction alerts or update a live dashboard.

The published interface is an interactive static snapshot. Current monthly alert is USD 10, separate from the USD 250 course allowance. Historical proposal claims are intentions.

Source: README.md; planning/runbooks/ingestion-runbook.md; dashboard/index.html

## 7. Dataset and analysis scope

- **Verified population:** 5,078,345 transactions and 5,177 positive labels, about 0.102%. Accepted source: Kaggle version 8.
- **Core files:** HI-Small transactions and account metadata. Patterns support optional evaluation enrichment.
- **Period:** September 1–18, 2022. Most transactions occur in the first ten days. Report the sparse tail separately.
- **Augmentation:** 285 historical FX records across 15 currencies. Findings describe a synthetic dataset.

Dataset: https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml . HI-Small is about 510 MB. Full Kaggle dataset is about 39 GB and must not enter Git.

Source: README.md; planning/archive/reviews/local-model-validation.md

## 8. Implemented source-to-dashboard flow

- **1. Source and control:** PowerShell starts the AWS acquisition wrapper. Lambda obtains Kaggle/FX and writes a versioned S3 manifest.
- **2. Shared preparation:** Step Functions reuses verified curated data or starts Glue. S3 retains Parquet and success markers.
- **3. Parallel analysis:** SageMaker verifies source/features and rules. Glue scores the frozen model. Athena aggregates treasury corridors.
- **4. Publication:** SageMaker export reconciles outputs. Lambda commits a verified release/current pointer. CloudFront serves it.

Optional training follows independently verified baseline processing. Streaming remains separate: CSV replay, Kinesis, Lambda, DynamoDB and S3 audit. Neither batch answer needs streaming.

Source: planning/runbooks/pipeline-optimisation-implementation.md; planning/runbooks/ingestion-runbook.md

## 9. AWS services and their roles

- **S3 and Glue:** S3 stores inputs, Parquet, models and evidence. Glue performs distributed curation and frozen inference.
- **Athena and SageMaker:** Athena runs corridor SQL. SageMaker Processing verifies/exports. Training creates optional candidates.
- **Workflow and Lambda:** Step Functions controls dependencies. Lambda acquires sources, manages contracts/locks and publishes.
- **Delivery and operations:** CloudFront serves the dashboard. SNS notifies failures. CloudWatch provides logs. Terraform defines resources.

ECR stores digest-pinned worker images. IAM separates component permissions. Kinesis and DynamoDB belong to the separate streaming path. No permanent model endpoint is needed for batch inference.

Source: README.md; planning/runbooks/pipeline-optimisation-implementation.md

## 10. A transaction through the pipeline

- **Raw identity:** Preserve source row identity, timestamp, sender/receiver, amounts, currencies and payment format.
- **Curated row:** Rename accounts, join metadata, validate fields, add FX amounts and strictly preceding sender history.
- **Business results:** Frozen inference adds a ranking score. Treasury adds the amount to directed bank and currency corridors.
- **Dashboard:** Independent checks reconcile every score and corridor before the queue and concentration views publish.

A row supports both questions. Treasury never uses risk score to determine volume. Is Laundering is an evaluation label, excluded from model inputs and the displayed investigation queue.

Source: planning/runbooks/pipeline-optimisation-implementation.md; planning/archive/reviews/local-model-validation.md

## 11. Acquisition and provenance

- **Default cloud mode:** AWS Lambda retrieves sources. The launcher needs no local dataset or Kaggle credential.
- **Input identity:** Version, hashes, file sizes and scope enter an immutable manifest and run-specific S3 prefix.
- **Source validation:** Missing, duplicate, future, stale or invalid FX stops acquisition before batch compute starts.
- **Local alternatives:** -LocalAcquisition uploads locally acquired sources. -PrepareOnly prepares without uploads or AWS jobs.

Cloud uses a verified versioned S3 source cache. Kaggle authentication, where required, belongs in the configured secret. Current cloud contract supports HI-Small and September 2022, not arbitrary subsets.

Source: planning/runbooks/ingestion-runbook.md

## 12. CSV schema and account joins

- **Duplicate Account headers:** Read by position and explicitly rename to from_account and to_account.
- **Composite identity:** Join metadata on bank ID plus account number. Account number alone can match the wrong bank.
- **String identifiers:** Keep leading zeros and stable source row IDs for reconciliation.
- **Patterns:** Natural-key matching needs collision protection. Unmatched attempt_id and pattern_type may remain null.

Patterns have no shared transaction ID and do not cover every positive. Keep is_laundering as the evaluation label. Pattern enrichment does not enter the frozen model.

Source: planning/big-data-pipeline-plan.md

## 13. Curation and Parquet storage

- **Validation:** Check required fields, valid amounts and the supported September 2022 contract.
- **Partitioning:** Write Parquet by txn_date. Avoid high-cardinality bank/account partitions.
- **Catalog access:** Use an explicit schema and Athena date partition projection rather than a crawler.
- **Commit proof:** A real encrypted _SUCCESS marks committed output. Partial files from failed jobs are invalid input.

Parquet enables selective column reads and reduces Athena scans. Optimization retains two G.1X workers and the existing causal-window implementation.

Source: planning/runbooks/aws-batch-runbook.md; planning/runbooks/pipeline-optimisation-implementation.md

## 14. Historical FX for two purposes

- **Treasury amount_usd:** Same-day historical FX supports descriptive USD-equivalent volume comparison.
- **AML amount_usd_asof:** Prior-calendar-day FX avoids introducing same-day information into historical scoring.
- **Coverage:** Frankfurter provides the main rates. Yahoo historical rates fill uncovered currency histories.
- **Limits:** Dataset timezone and intraday publication timing remain unverified. Values are historical valuations.

Sources: https://frankfurter.dev/ and https://finance.yahoo.com/ . Prior-day inputs are not a claim of exact intraday availability. Required dates/currencies and seven-day freshness are validated. Compare FX-normalized amounts rather than raw yen/rupiah/USD values.

Source: dashboard/index.html; planning/runbooks/ingestion-runbook.md; planning/archive/reviews/local-model-validation.md

## 15. Causal behaviour features

- **Transaction counts:** Prior outgoing counts over 1 hour, 24 hours and 7 days for each bank/account sender.
- **Counterparty counts:** Distinct prior counterparties over 24 hours and 7 days.
- **Window boundary:** Use [t minus window, t). Exclude the current transaction and all same-timestamp peers.
- **History limits:** Before September 1, no history is available. Early observations have truncated lookback.

These are the five rolling fields in the current feature contract. Complete sender outgoing history is needed. Incoming/outgoing balance and bank-change proposal features should not be presented as current model inputs.

Source: src/bda_pipeline/analytics.py; planning/archive/reviews/local-model-validation.md

## 16. Rule baseline

- **ACH:** ACH adds 50 points, based on its earlier transaction-level association in the synthetic EDA.
- **Amount:** Above the training payment-format P99 of prior-day USD amount adds 20 points.
- **Behaviour:** Prior-hour count above 10 or prior-day distinct counterparties above 15 adds 30 points.
- **Interpretation:** Rule score at least 60 defines rule charts. Model ranking uses a separate scale with no alert threshold.

Rules are a benchmark, not universal AML facts. Initial behaviour weights were hypotheses. Self-transfer and currency mismatch remain possible model features but do not add rule points.

Source: dashboard/index.html; src/bda_pipeline/analytics.py

## 17. Frozen XGBoost model

- **50 inputs:** Log USD amount, causal counts, flags, cyclic hour and categories from training-only vocabularies.
- **Excluded information:** Labels, IDs, absolute dates, patterns, rule reasons and same-day FX stay outside the matrix.
- **Selection and artifacts:** Validation selected depth 5 with 120 trees. Keep UBJ, feature order, schema and hashes together.
- **Training versus scoring:** Glue scores the active frozen model. -Train creates a verified private candidate without replacement.

Feature inputs are float32. Training-only categorical vocabularies include unseen-category indicators. The training class weight was 1283.158498 negatives per positive. Weighted outputs are not calibrated probabilities.

Source: planning/archive/reviews/local-model-validation.md; planning/runbooks/ingestion-runbook.md

## 18. Chronological evaluation

- **Train: Sep 1–6:** 3,248,921 rows and 2,530 positives. Learn categories, thresholds and model parameters.
- **Validate: Sep 7–8:** 965,524 rows and 1,036 positives. Select the candidate before loading test labels.
- **Test: Sep 9–10:** 862,792 rows and 956 positives. Evaluate a fixed top-100 review capacity.
- **Tail: Sep 11–18:** 1,108 rows and 655 positives. Its different prevalence needs separate reporting.

Random row splits can leak historical behaviour and split multi-hop attempts. Earlier whole-dataset EDA makes results exploratory. Further tuning needs a newly designed evaluation protocol rather than reuse of the observed holdout.

Source: planning/archive/reviews/local-model-validation.md

## 19. Observed ranking performance

- **Precision at 100:** Model: 44 positive labels among 100 reviews. Rules: 0 among 100 on the same main test.
- **Recall at 100:** 44 of 956 positives gives 4.60%. Most positives remain outside this small queue.
- **Average precision:** Model 0.135546 versus rules 0.006758, measuring ranking over score thresholds.
- **Operational meaning:** Review yield improves, while 56 of the top 100 still have negative synthetic labels.

Average precision uses stepwise tied-score area, not trapezoidal PR-AUC. Test lift@100 was 397.10. Accuracy is uninformative with about 0.1% positives. Metrics remain unchanged after full-cloud reconciliation.

Source: planning/archive/reviews/local-model-validation.md; planning/evidence/pipeline-optimisation-result-20261006.json

## 20. Model explanations

- **Evidence:** Three largest signed TreeSHAP contributions to the model logistic margin for each displayed transaction.
- **Positive contribution:** Raises the model margin relative to its baseline.
- **Negative contribution:** Reduces the margin. Bias plus full contributions reconciles to the predicted margin.
- **Investigator reading:** Explains model behaviour, without proving the cause of laundering or assigning guilt.

One-hot suffixes refer to saved vocabulary indexes, not ordinal values. The full contribution sum is verified. Labels remain absent from queue details.

Source: planning/archive/reviews/local-model-validation.md; dashboard/index.html

## 21. Treasury corridor analysis

- **Directed bank pair:** Sending bank and receiving bank. Reverse direction is a separate corridor.
- **Directed currency pair:** Payment currency and receiving currency, aggregated separately from bank pairs.
- **Outputs:** USD volume, transaction count, volume share, rankings and daily changes.
- **Business interpretation:** Exposure and dependency review. A large corridor does not establish AML suspiciousness.

Acceptance reconciled 1,082,185 daily corridor rows with the full population. Keep bank and currency grains separate to avoid double counting. Current marts do not retain payment-format mix.

Source: dashboard/index.html; planning/runbooks/pipeline-optimisation-implementation.md

## 22. Shares and concentration

- **Share:** Corridor USD volume divided by total USD volume for the selected period and grain.
- **HHI:** Sum of squared volume shares on a 0–1 scale. Higher means greater concentration.
- **Complete denominator:** Include every corridor even when tables display only the top 15.
- **Trend interpretation:** Read total volume and concentration together. Mix can concentrate while total volume falls.

Illustrative examples, not findings: two equal shares of 0.5 give HHI 0.5. Ten equal shares of 0.1 give HHI 0.1. Bank-pair concentration differs from concentration by a single sending bank.

Source: dashboard/index.html

## 23. Workflow and publication gates

- **Run ownership:** A conditional lock controls competing runs. Every execution owns isolated output prefixes and workers.
- **Parallel analysis:** Baseline/source verification, frozen scoring and Athena analysis run after curation.
- **Export checks:** Full native score parity, independent corridor reconciliation and source/FX/history integrity.
- **Release selection:** Publish immutable files, then conditionally update current.json only after all checks pass.

Optional candidate training depends on verified baseline. A failed branch cannot publish unverified results. Compute-end timestamps do not prove committed output because service finalization and upload still matter.

Source: planning/runbooks/pipeline-optimisation-implementation.md

## 24. Release reuse and stage reuse

- **Release cache:** Matching unchanged verified inputs return the existing successful release without new batch work.
- **Stage cache:** Compatible runs reuse curated/scored objects and still perform complete verification.
- **Semantic identity:** Source hashes, scope, schema, runtime and code enter keys. Scoring also binds the model/package.
- **Forced rebuild:** -Rerun bypasses both caches. -Refresh requests fresh source download and serves a different purpose.

Stage receipts expire after 30 days. Only verified published runs register receipts. Reuse checks inventories, encryption, original proof and ETags. Conditional copies isolate new output and copy _SUCCESS last. Changed or corrupt receipts fail closed.

Source: planning/runbooks/pipeline-optimisation-implementation.md; planning/runbooks/ingestion-runbook.md

## 25. Measured execution times

- **Original rebuild:** 24m59s for the earlier verified source-to-result run.
- **Optimized rebuild:** 20m55s for 5,078,345 rows, 16.3% faster in one observed comparison.
- **Stage reuse plus training:** 18m02s, zero new Glue jobs, and a verified private candidate.
- **Unchanged release reuse:** 33.85s with no batch workers. This returns an existing result.

These are individual observations, not an SLA or percentile. Scores, corridor reconciliation and business rankings remained unchanged. Parallel branches produced the main measured saving. Incremental ingestion remains future work.

Source: planning/evidence/pipeline-optimisation-result-20261006.json

## 26. Repository navigation

- **Application:** src/bda_pipeline/ and jobs/: acquisition, transformations, features, scoring and verification.
- **AWS resources:** infra/: Terraform stacks. cloud-ingestion/, sagemaker/ and streaming/: worker entry points.
- **User tools:** scripts/: run/verification commands. dashboard/: static interface. sql/: analysis queries.
- **Reference:** planning/: runbooks, diagrams, evidence and archive. dataset/ and output/: ignored generated files.

Group 3 work belongs in bda-project-g3/. bda_repo_caldo/ is a read-only friend reference. Do not commit datasets, credentials, private state or keys.

Source: README.md; scripts/README.md

## 27. Windows setup

- **Prerequisites:** Python 3.11 or later and AWS CLI v2. Browser sign-in requires CLI 2.32.0 or newer.
- **Environment:** Run from the Group 3 repository. The cloud extra supports the deployed launcher.


```powershell
cd ".\bda-project-g3"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[cloud]"
.\scripts\aws\configure-aws.ps1
```

Installing and signing in do not deploy a new account. If activation is blocked, use .\.venv\Scripts\python.exe for pip and the launcher’s -Python option.

Source: README.md; planning/runbooks/aws-login.md

## 28. AWS login and Python selection

- **Profiles:** is459-g3 holds browser login. is459-g3-tools forwards temporary credentials to project tools.
- **Guard:** The helper checks account and Singapore region. Settings remain under %LOCALAPPDATA%/IS459-G3.


```powershell
.\scripts\aws\configure-aws.ps1
# SSO alternative for an organization access portal
.\scripts\aws\configure-aws.ps1 -LoginMethod SSO
# Explicit venv executable, without activation
.\scripts\ingestion\ingest.ps1 -Python .\.venv\Scripts\python.exe `
  -Status <run-id>
```

Use SSO only with your administrator’s start URL and SSO region. Passwords/MFA belong on AWS’s sign-in page. Re-run the helper when the session expires. Missing SignInLocalDevelopmentAccess requires administrator review.

Source: planning/runbooks/aws-login.md; scripts/aws/configure-aws.ps1

## 29. Default pipeline command

- **Pipeline:** AWS acquisition, curation, verification, frozen scoring, treasury analysis and dashboard publication.
- **Result:** Returns run ID, execution link, progress and release links. Uncached execution starts paid jobs.


```powershell
.\scripts\ingestion\ingest.ps1 -Python python

# Submit and let AWS continue
.\scripts\ingestion\ingest.ps1 -Python python -NoWait

# Inspect the returned run ID
.\scripts\ingestion\ingest.ps1 -Python python -Status <run-id>
```

-NoWait returns after AWS accepts the workflow. Processing continues in AWS when the terminal closes. This reference creation launches no cloud processing. The command assumes current credentials and the existing deployed account.

Source: planning/runbooks/ingestion-runbook.md; scripts/ingestion/ingest.ps1

## 30. Smoke, training and rebuild

- **Smoke:** -Rows 1000 publishes a clearly labelled small release. It checks plumbing rather than model quality.
- **Training:** -Train uses the full subset and rejects -Rows. A verified candidate remains private.


```powershell
.\scripts\ingestion\ingest.ps1 -Python python -Rows 1000

.\scripts\ingestion\ingest.ps1 -Python python -Train

# Bypass release and stage caches
.\scripts\ingestion\ingest.ps1 -Python python -Rerun -NoWait
```

A smoke can become the root-selected dashboard, so use its immutable link and identify its scope. Stage reuse may avoid Glue jobs but training and verification still cost money. -Rerun forces processing and needs sufficient budget.

Source: planning/runbooks/ingestion-runbook.md

## 31. Source controls and local preparation

- **Source control:** Version pinning requires a supported Kaggle version. -Refresh requests fresh source download.
- **Preparation:** -PrepareOnly uploads nothing and starts no AWS job, but may contact Kaggle and FX APIs.


```powershell
.\scripts\ingestion\ingest.ps1 -Python python -DatasetVersion 8
.\scripts\ingestion\ingest.ps1 -Python python -Refresh
.\scripts\ingestion\ingest.ps1 -Python python -LocalAcquisition
.\scripts\ingestion\ingest.ps1 -Python python -PrepareOnly -Rows 1000 `
  -Deployment <verified-deployment.json>
```

Version 8 is the accepted source in retained evidence. -FxFile and -Deployment are rejected in default cloud mode. Local acquisition/preparation needs .[ingest] and local Kaggle access. Replace paths with verified files.

Source: planning/runbooks/ingestion-runbook.md

## 32. Inspection and release verification

- **Run inspection:** Inspect progress with the exact run ID and check wrapper/child history for the stopped stage.
- **Publication proof:** Verify immutable release files and whether another run has become current.


```powershell
python scripts/ingestion/inspect-ingestion.py `
  --run-id <run-id> --output output/run-progress

python scripts/validation/verify-ingestion-release.py `
  --run-id <run-id> --output output/run-verification
```

These scripts read cloud/public data and write local reports. They do not start batch jobs. Use fresh output paths. A root dashboard can show a later publication. Prefer immutable links or hard refresh when a browser has old JavaScript.

Source: planning/runbooks/ingestion-runbook.md

## 33. Failure handling and recovery

- **Diagnosis:** Read failure.json, workflow history and logs. Acquisition has separate acquisition-failure.json evidence.
- **Guarded recovery:** Unlock requires an unsuccessful terminal workflow, owned lock and no active Glue/SageMaker workers.


```powershell
.\scripts\ingestion\ingest.ps1 -Python python -Status <failed-run-id>

# Recovery mutation after terminal and worker checks
.\scripts\ingestion\ingest.ps1 -Python python -Unlock <failed-run-id>
```

Paid jobs are not automatically resubmitted. Cancellation is run-scoped and event delivery asynchronous. Retain evidence/partial files. Never reuse the failed ID or manually delete locks while workers run. After resolving the issue, use a fresh ID for an approved retry. Acquisition failures before a child workflow have no batch lock.

Source: planning/runbooks/ingestion-runbook.md; planning/runbooks/pipeline-optimisation-implementation.md

## 34. Dashboard entry and provenance

- **Dashboard:** https://d1n61tlw9pcrog.cloudfront.net/ serves the selected verified snapshot.
- **Identity:** Check source scope, dates, model identity and release provenance before interpreting numbers.
- **Stable evidence:** Use the immutable link returned by a run for reproducible recordings and comparisons.
- **Behavior:** Controls explore precomputed outputs. Opening the site does not retrain, run ETL or activate streaming.

The repository records successful publication. This reference does not freshly test availability. Current root data can change after another publication. The interface is an interactive static snapshot.

Source: README.md; dashboard/index.html

## 35. AML review walkthrough

- **1. Choose scope:** Open AML review. Select test, validation or sparse tail, then frozen model or rule baseline.
- **2. Compare:** Read precision@100, recall@100 and average precision for the same period and capacity.
- **3. Inspect evidence:** Select a queue row to view transaction details and model contributions or rule reasons.
- **4. Export:** Export shown queue as CSV. Rule chart thresholds remain separate from model ranking.

Test: September 9–10. Validation: September 7–8. Tail: September 11–18. Queue ties use timestamp then source row. Labels are omitted. Scores on either scale are uncalibrated, and the model queue is experimental.

Source: dashboard/index.html; dashboard/dashboard.js; planning/archive/reviews/local-model-validation.md

## 36. Treasury walkthrough

- **1. Select period:** Open Treasury corridors. Choose all 18 days or a single day.
- **2. Read rankings:** Inspect directed bank pairs and payment-to-receiving currency pairs by USD volume.
- **3. Compare trends:** Read daily volume alongside bank-pair HHI. Top-15 rankings retain full-population denominators.
- **4. Export detail:** Export shown bank corridors with the period and metric grain stated clearly.

Do not promise proposal-only payment-format filters, Sankey charts or every underlying transaction drilldown. High volume and high HHI do not establish suspicious activity.

Source: dashboard/index.html; dashboard/dashboard.js

## 37. Separate streaming architecture

- **Producer and Kinesis:** Replay approved CSV with original timestamps. Hash bank/account composite keys to preserve sender order.
- **Lambda and DynamoDB:** Recompute causal features and frozen scores. Conditional transactions commit state and unique results.
- **Audit and failures:** S3 keeps scored audit. Processing failures stop the sequence and use archives/notifications.
- **Separate scope:** Streaming does not power the batch dashboard or configure high-risk transaction alerts.

One provisioned shard and Lambda concurrency/parallelization one support bounded ordering. Per-sender ordinals detect missing events. Reject labels and precomputed histories. Exclude same-timestamp peers and fail closed on exhausted state limits.

Source: streaming/README.md

## 38. Streaming acceptance and limits

- **Verified cohort:** 884 transaction comparisons and 32 sender-history checks passed the completed replay.
- **Duration:** 1,049.545 seconds in the single accepted run. This does not measure live ingestion latency.
- **Retained state:** Consumer disabled. A provisioned shard can continue charging without a consumer.
- **New execution:** Review image, cohort, cost and approval before another cloud replay. Review retained image scan findings.

The producer cap is ten events/second, but sequential acknowledgements affected actual duration. Duplicates repair audit without advancing state. Missing/out-of-order events stop scoring. The original image had 8 HIGH, 6 MEDIUM and 1 LOW OS-package findings.

Source: streaming/README.md; planning/archive/reviews/streaming-demo-result-20261006.md

## 39. Local streaming preparation

- **Default dry run:** The producer makes no AWS calls. Use new package paths and verified local files.
- **Checks:** Targeted tests cover streaming semantics. Cloud execution remains a separately authorized operation.


```powershell
python scripts/streaming/prepare-streaming-demo.py `
  --reference <verified-bq1_scored.parquet> `
  --artifacts <verified-artifacts-directory> `
  --output output/streaming-demo-new
python scripts/streaming/replay-streaming-demo.py --package output/streaming-demo-new
python -m pytest -q tests/test_streaming.py `
  tests/test_streaming_aws.py tests/test_streaming_verification.py
```

Requires the local ML/dev environment and verified artifacts. Placeholders are templates. Do not add --execute-approved or activate the deployed mapping for an unapproved rehearsal.

Source: streaming/README.md

## 40. Local development and tests

- **Dependencies:** dev, ml and ingest extras support tests, modeling and local acquisition.
- **Parity:** Spark runs separately in the official Glue container with Docker and runbook fixtures.


```powershell
python -m pip install -e ".[dev,ml,ingest]"
python -m pytest tests --ignore=tests/spark -q

# Docker and fixtures: see batch runbook
.\scripts\validation\test-glue-local.ps1
.\scripts\validation\test-cloud-scoring-local.ps1
```

Latest recorded non-Spark suite: 136 passed, one skipped. No project test suite was rerun to make this deck. Terraform mock tests live under each stack. Consult the batch runbook before Spark commands.

Source: README.md; scripts/README.md

## 41. Offline dashboard rehearsal

- **Snapshot files:** Serve index.html, dashboard.css, dashboard.js and dashboard.json together from the existing site output.
- **Browser:** Open http://127.0.0.1:4174/. Stop with Ctrl+C. This starts no AWS work.


```powershell
python -m http.server 4174 --bind 127.0.0.1 `
  --directory output/cloud-dashboard-release-20261006/site
```

The documented retained directory may represent an earlier release. Check embedded provenance. dashboard/ source alone lacks generated dashboard.json and cannot recreate the snapshot.

Source: planning/delivery/final-demo-runbook.md

## 42. Terraform and deployment boundaries

- **Separate stacks:** Foundation, scoring, hosting, orchestration, ingestion, SageMaker and streaming use Terraform.
- **Existing account:** Initial deployment already completed. Original scripts guard their original resource additions.
- **Reviewed changes:** Prepare artifacts, validate hashes and inspect an exact scoped plan before an approved update.
- **Private state:** Variables, plans and state remain under %LOCALAPPDATA%/IS459-G3, outside Git.

Do not repeat deploy-ingestion.ps1 -Execute against populated state: it expects the original 33 additions. Default-off streaming configuration can destroy retained resources if applied to populated state. Subsequent updates need a reviewed change plan.

Source: infra/README.md; planning/runbooks/ingestion-runbook.md; planning/runbooks/pipeline-optimisation-implementation.md

## 43. Security and operational controls

- **Access:** Component roles and bounded scopes constrain writes. Credentials stay in profiles or configured secrets.
- **Storage:** Private origins, S3 public-access blocks and encryption protect inputs/artifacts. Only approved site assets publish.
- **Compute bounds:** Finite runtimes/status checks and zero automatic paid resubmission constrain runaway execution.
- **Traceability:** Logs, hashes, success markers and reconciliation support diagnosis and reproducibility.

Never include credentials, private variables/state, keys, alert email addresses or login screens in shared videos/slides. Recorded hosting acceptance checked HTTPS and origin denial, not universal vulnerability freedom.

Source: planning/runbooks/pipeline-optimisation-implementation.md; planning/aws-deployment-status.md

## 44. Cost controls and cleanup

- **Budgets:** USD 250 total course allowance. USD 10 monthly awareness alert at 50%, 80% and 100%. Alerts do not stop spending.
- **Paid operations:** Uncached Glue/SageMaker work, Athena queries and replay add cost. Reuse reduces repeated work.
- **Retained costs:** Hosting/storage and the provisioned shard can keep charging after workers stop.
- **Cleanup:** After presentation, review all stacks, bucket versions, ECR images and retained artifacts before approved deletion.

No fresh account spend or current AWS price quote is claimed. Historical estimates are not invoices. Verify both resource absence and later billing after cleanup. Disabling the streaming consumer does not terminate shard capacity.

Source: README.md; planning/delivery/final-demo-runbook.md; streaming/README.md

## 45. Demo and submission preparation

- **Narrative:** Business decisions, architecture, curation, model results, both views and workflow evidence.
- **Videos:** Record real setup and end-to-end execution with narration or annotations. Retain clips for long compute stages.
- **Contributions:** List actual team work and assign each member a component. Do not infer authorship from suggested roles.
- **Appendix:** Add real YouTube links, accessible source/artefact links and editable diagrams before submission.

This reference is not a complete course submission. Recorded videos, shared links and actual contribution attribution require team completion. Screenshot collections or video plans cannot replace setup/run recordings.

Source: planning/delivery/final-demo-runbook.md; ../[IS459] Project_G1.pptx

## 46. Common problems and first checks

- **Login or account:** Rerun configure-aws.ps1 and confirm project profile/region before changes.
- **Python modules:** Use the same interpreter for pip and -Python. Install the correct extras.
- **Old dashboard:** Use the immutable release, check scope/provenance, then hard refresh. A smoke may be current.
- **Failed workflow:** Inspect status/evidence. Confirm owned workers stopped before guarded unlock and a fresh reviewed retry.

Acquisition failures need acquisition evidence, while partial Parquet needs success markers and parity. Avoid generic state reset or manual lock deletion. A fresh ID separates retry evidence from the failed run.

Source: planning/runbooks/ingestion-runbook.md; planning/runbooks/aws-login.md

## 47. Architecture source appendix

- **Current logic:** Latest optimization flow is documented in pipeline-optimisation-implementation.md.
- **Editable diagrams:** planning/architecture/ contains draw.io sources. The exported images on the next slides retain their source labels.
- **Revision caveat:** Existing drawings predate the latest cloud-acquisition and cache optimization. Use the current flow on slide 8 for operation.
- **Historical predecessor:** automated-ingestion.drawio depicts the local-acquisition predecessor and should not replace current status.

This deck preserves diagram evidence instead of silently relabeling old drawings as the latest implementation. No draw.io source was modified. The native text explanations and operational commands document the current verified flow.

Source: planning/runbooks/pipeline-optimisation-implementation.md; planning/architecture/automated-ingestion.md

## 48. BQ1 architecture diagram


Source drawing dated 6 October 2026, exported before the latest acquisition/cache update. See slide 8 and the optimization runbook for the current full wrapper. Editable source: planning/architecture/aml-pipeline-architecture.drawio. The diagram image preserves existing architecture evidence.

Source: planning/architecture/exports/bq1.drawio.png

## 49. BQ2 architecture diagram


Source drawing dated 6 October 2026, exported before the latest acquisition/cache update. See slide 8 and the optimization runbook for the current full wrapper. Editable source: planning/architecture/aml-pipeline-architecture.drawio. Treasury analysis remains separate from transaction risk ranking.

Source: planning/architecture/exports/bq2.drawio.png

## 50. Reference sources

- **Overview:** README.md, planning/README.md and scripts/README.md
- **Operations:** planning/runbooks/ingestion-runbook.md and pipeline-optimisation-implementation.md
- **Evidence and method:** pipeline-optimisation-result-20261006.json and archive/reviews/local-model-validation.md
- **Delivery:** Instructor brief, final-demo-runbook.md, streaming/README.md and infra/README.md

Repository paths are relative to bda-project-g3 unless prefixed ../. Current evidence supersedes historical proposal statements. External URLs identify data sources rather than claims of fresh web research.

Source: README.md
