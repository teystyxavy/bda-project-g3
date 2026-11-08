# HI-Medium AWS walkthrough and voiceover script

Recording target: `medium-full-20261030-v2`, Singapore region.
Status: AWS console footage captured on 30–31 October 2026. The finished video, actual chapter timings, voiceover script and optional captions are under `output/hi-medium-walkthrough-20261030/`. Use `VOICEOVER-SCRIPT.md` there for the exact recorded sequence; the numbered sections below are the original capture plan, not the final timecodes. This is an inspection of a completed execution, with no new batch started for the recording. SageMaker's log shortcut was blocked by the browser's URL access policy; the completed processing-job pages and retained S3 evidence are included instead. Glue CloudWatch completion logs are included.

## Recording approach

Record an inspection of the completed execution, with that description visible at the start. Do not imply that opening the pages reruns the pipeline. Show the real AWS pages and drill into each relevant run, input, output and log. Hold each view while its narration is read. Leave two seconds before and three seconds after each spoken passage. Remove loading screens and label cuts between processing stages. Do not record sign-in.

This is the HI-Medium local-acquisition route. It does not use the HI-Small acquisition Lambda or dashboard Start ingestion control. Do not demonstrate those as the Medium entry point. Candidate training was not requested. Streaming is a separate component and is not part of this batch execution.

The instructor's brief, slide 7, requires coverage of both setup and running the pipeline, with narration or annotations, and YouTube links in the slides appendix. The recording can be divided at the setup/run boundary. The 15-minute final presentation/demo allowance is separate; use selected excerpts there if the full recording runs longer.

## Setup segment

### 01 — Project and purpose (35 seconds)

Show the project README and the HI-Medium runbook. Then show the dataset name and row count in the published release.

**Say:** “This walkthrough follows our HI-Medium ingestion, containing about 31.9 million synthetic transactions. The pipeline answers two questions: which transactions should investigators review first, and where is bank-to-bank transaction volume concentrated? I will show the setup and then follow the completed run through its actual AWS pages.”

### 02 — Local prerequisites and source acquisition (45 seconds)

Show the README setup commands and Medium runbook commands. Show the existing transaction CSV, accounts CSV and download manifest by filename and size, without opening millions of rows. Show a small source preview if available. Commands are instructions unless visibly executed; do not fabricate terminal output.

**Say:** “The launcher needs Python, the project dependencies and AWS CLI access. We sign in outside the recording. HI-Medium is downloaded locally from Kaggle version eight, and valid files are reused. The inputs include transactions and account metadata. This route differs from HI-Small, whose download can run inside an AWS Lambda function.”

### 03 — Infrastructure and access (45 seconds)

Show the existing Terraform stack folders, then the project-specific IAM processing roles and their service trust. Show the relevant permissions without opening credentials, private state or secret values. Show ECR's existing worker image and digest.

**Say:** “Terraform defines the AWS resources. Each processing component uses its own permissions. The worker image is stored in Amazon ECR and pinned to an exact version. We inspect the existing deployment here. Starting from a new account would also require reviewing and applying the documented infrastructure plans before running ingestion.”

### 04 — Launch configuration (35 seconds)

Show the implemented full-run command from the Medium runbook: `.\scripts\ingestion\ingest.ps1 -Subset HI-Medium -LocalAcquisition -DatasetVersion 8`. Show the retained submission record. Do not execute a new paid run just for footage.

**Say:** “This command selects HI-Medium, local acquisition and dataset version eight. There is no row limit, so it processes the full subset. The launcher prepares the inputs, uploads them to S3 and starts Step Functions. For this recording, we inspect the retained execution rather than launching the same paid work again.”

## Run segment: follow each AWS page

### 05 — S3 raw inputs (40 seconds)

Open the project data bucket and `pipeline/runs/medium-full-20261030-v2/`. Follow the actual manifest paths: inputs may reference the preceding attempt's retained upload. Open the raw prefix and each transaction/account/FX object Properties page. Show sizes and encryption. Never substitute an empty v2 raw folder for the actual input location.

**Say:** “S3 stores the raw inputs used by this execution. The manifest points to the exact transaction, account and exchange-rate files. This retry reused the verified upload from the earlier attempt. Reusing source files avoids another download while preserving the identity of the inputs.”

### 06 — Manifest and FX (40 seconds)

Open the raw manifest and a safe FX preview. Show subset, expected rows, source hashes, date range and FX coverage. Exclude unrelated private configuration.

**Say:** “The manifest tells the pipeline what to expect: the dataset version, file hashes and 31,898,238 transactions. The source covers September first to twenty-eighth, 2022. Historical exchange rates make currencies comparable. Treasury uses same-day values, while the AML features use prior-day rates to avoid looking ahead.”

### 07 — Step Functions execution overview (40 seconds)

Open `is459-g3-ingest`, select `medium-full-20261030-v2`, and show status, start/end times and execution graph. Confirm the displayed values before using the narration.

**Say:** “Step Functions coordinates the batch. This execution began on October thirtieth and completed successfully. Its graph connects curation, parallel analysis, verification and publication. We are looking at a completed execution, so the processing delays have already elapsed.”

### 08 — Execution input and validation (35 seconds)

Show execution Input and the Begin state's input/output. Explain the manifest validation and single-run lock. Inspect the actual payload for secrets before recording it.

**Say:** “The execution starts with a run identifier. The control function checks the manifest and inputs, then checks whether verified processing stages can be reused. It also prevents conflicting runs from publishing at the same time. These checks happen before the expensive processing begins.”

### 09 — Lambda control and execution history (35 seconds)

Follow Begin's actual Lambda resource link. Show Configuration and the corresponding CloudWatch log stream. Return to Step Functions Events and the Curate state.

**Say:** “Lambda performs the short coordination tasks. CloudWatch records what happened, while the execution history connects each task to its result. We can follow a failure back to the relevant step instead of guessing which service failed.”

### 10 — Glue curation run (45 seconds)

Open job `is459-g3-ingest-curate`, Runs, and run `jr_d6f25b391dd0bc1fa29569ca4280205d108e98447de026362705bb8a8454b3ff`. Show status, actual run arguments, four G.1X workers and the run's timeout rather than only job defaults.

**Say:** “AWS Glue cleans the transaction data and builds behavioural features. This Medium run used four workers. It preserves account identifiers as text and joins account metadata using both bank and account identifiers. The curation job completed successfully before the analysis branches began.”

### 11 — Glue script and logs (45 seconds)

Show the actual curation script and linked CloudWatch logs. Point to explicit source-column renaming, FX processing, preceding-history features and the write step. Use the source file view if the console does not show the full script.

**Say:** “The source has two columns called Account, so the script explicitly separates sender and receiver. It checks data quality, adds historical FX values and calculates behaviour using earlier transactions only. These features describe activity before each transaction, which helps prevent future information from leaking into its score.”

### 12 — S3 curated output and Glue catalog (40 seconds)

Follow Curate's real output path to S3. Show date partitions and Parquet objects. Open the run-specific Glue catalog table, schema, location and partition projection configuration where applicable.

**Say:** “The cleaned output is stored as Parquet and organised by transaction date. Parquet reduces the data that later jobs and queries need to read. The Glue catalog describes the columns and location so Athena can query the curated files.”

### 13 — Parallel analysis (30 seconds)

Return to the execution graph and select ScoreAndAnalyze. Show all three actual branches.

**Say:** “The workflow now runs three branches in parallel: frozen-model scoring, treasury corridor analysis and independent baseline verification. They all use the same curated input. The workflow waits for the required branches before building the final release.”

### 14 — Glue scoring run and model artifacts (45 seconds)

Open `is459-g3-ingest-score`, run `jr_ee8892a47fef00bd644561726feeace3a363e0d5fb199a36c644e031b0efcd5c`, its arguments, script and linked log stream. Follow its actual model/schema artifact paths in S3.

**Say:** “The second Glue job applies the existing frozen XGBoost model to every transaction. The model and feature schema are versioned together. This run uses the model originally trained on HI-Small; it does not train a new Medium model. Its score ranks review priority and is not a probability of laundering.”

### 15 — Scored output (35 seconds)

Open the scored S3 prefix and actual success marker. Show a safe schema or retained verification summary rather than downloading the full dataset.

**Say:** “Scored Parquet retains the transaction identity and model output. The completed output is checked before it can be used in the dashboard. Our retained score proof covers all 31,898,238 rows and records zero difference from the independent frozen-model calculation.”

### 16 — Athena workgroup and query history (35 seconds)

Open workgroup `is459-g3-analysis`, query history and query `213e730d-7267-4566-a37d-510dd3aa0afd`. Show successful status and the query's scan statistics. Do not execute a new query.

**Say:** “Athena answers the treasury question using the curated data in S3. This workgroup provides the project's query settings and scan limit. The retained corridor query succeeded and scanned about 573 megabytes. We can inspect its SQL and existing results without rerunning it.”

### 17 — Athena SQL, results and output object (45 seconds)

Show the actual SQL, result columns, and the linked S3 result object. Identify daily bank pairs separately from currency pairs.

**Say:** “The SQL groups USD-equivalent volume by day and directed corridor. A bank corridor records which bank sends and which receives. Currency corridors are a separate grouping. These results measure treasury exposure and concentration; a large corridor is not automatically suspicious.”

### 18 — SageMaker baseline processing (45 seconds)

Open Processing jobs and `is459-g3-baseline-medium-full-20261030-v2`. Show completed status, input/output locations, container image, resource configuration and linked logs.

**Say:** “SageMaker Processing checks the rule baseline and source consistency. This is a processing job, not a new model-training job. It provides a comparison for the model and checks the data used in the business outputs. Medium uses a larger disk allocation because its input is much larger than HI-Small.”

### 19 — Baseline outputs and optional training (30 seconds)

Follow baseline output locations in S3. Return to Step Functions and show the training choice for this run.

**Say:** “The baseline outputs are retained in S3. Candidate training was not requested for this HI-Medium execution. The pipeline therefore keeps the existing frozen model. A different model would require its own training, evaluation and promotion decision.”

### 20 — SageMaker export processing (45 seconds)

Open `is459-g3-export-medium-full-20261030-v2`. Show completed status, inputs, outputs, resource configuration and CloudWatch logs.

**Say:** “The export processing job brings the scored transactions and treasury results together. It independently checks model scores, source counts, exchange rates and corridor totals. It then builds the compact files used by the dashboard. Publication depends on these checks passing.”

### 21 — Verification evidence and release files (45 seconds)

Follow the actual S3 evidence and export paths from the job. Open safe count/parity/reconciliation summaries, then show `index.html`, stylesheet, JavaScript and `dashboard.json` in the release. Verify names in the live bucket.

**Say:** “These evidence files connect the release to its inputs and processing results. The counts must reconcile, and corrupted scores must fail verification. The dashboard is a static release containing the interface and a data snapshot. This makes its published results traceable to a particular run.”

### 22 — Publish Lambda and final workflow output (40 seconds)

Return to the Publish state, follow its Lambda resource and corresponding log entry, then show the execution output with `status: published`, release URL, no training and no promotion.

**Say:** “The publication step verifies the release and copies it to the private dashboard origin. It updates the current-release pointer and refreshes delivery. The execution output identifies this Medium release as published. It also records that no new model was trained or promoted.”

### 23 — Hosting S3 bucket (35 seconds)

Open the dashboard bucket, the immutable Medium release prefix, and current pointer object's safe content. Show Block Public Access and encryption without changing settings.

**Say:** “S3 keeps an immutable copy of this release alongside the pointer used by the main dashboard link. The origin bucket remains private. Keeping release-specific paths lets us identify exactly which result was shown, even if the main dashboard later moves to another run.”

### 24 — CloudFront distribution (35 seconds)

Open the distribution matching `d1n61tlw9pcrog.cloudfront.net`. Show status, domain, origin access and relevant cache behaviour. Show publication invalidation only if present in the real history.

**Say:** “CloudFront delivers the dashboard over HTTPS. Its origin access configuration lets it read the private S3 website files. Users can view the published business outputs without receiving direct access to the processing bucket or the AWS console.”

### 25 — AML dashboard and transaction evidence (45 seconds)

Open the immutable Medium release. Show the run ID and all-row scoring coverage. Scroll to the top-ranked queue, select a transaction and show its explanation.

**Say:** “The AML view shows that every transaction received a score. Investigators use the ranked queue to decide what to review first, then open a transaction for supporting evidence. The explanation shows which features influenced the ranking. It supports investigation; it does not prove that a transaction is criminal.”

### 26 — Model evaluation (40 seconds)

Show Test September 9–10, whole evaluation population and capacity 100. Open Model evaluation and the baseline comparison.

**Say:** “In this Medium test period, 39 of the model's top 100 transactions have positive labels, compared with zero for the rule baseline. Recall is about 0.91 percent, so most positives remain outside that queue. This is transfer evaluation on synthetic data that may overlap with the original model population.”

### 27 — Treasury dashboard (45 seconds)

Open Treasury corridors. Show all 28 days, corridor rankings, concentration trend, a selected corridor and day filter. Demonstrate an existing CSV export if useful.

**Say:** “The treasury view covers the full 28-day dataset. It shows where USD-equivalent volume is concentrated and how that changes over time. Selecting a corridor reveals its history. Treasury can use this to examine liquidity exposure and dependence on particular bank routes, separately from AML review.”

### 28 — Monitoring and failure handling (40 seconds)

Show project-specific CloudWatch log groups, the workflow failure path, and the SNS failure topic without displaying subscribers' addresses. Show a historical failure only if clearly labelled as the earlier attempt.

**Say:** “CloudWatch logs provide evidence for each processing stage. If a required stage fails, the workflow follows its failure handling and notification path. The initial Medium attempt was rejected before a Glue job started; the successful retry used explicit worker settings and the retained inputs.”

### 29 — Budget and cleanup (35 seconds)

Show only the project budget and its configured amount. Distinguish it from actual spend. Show the cleanup runbook without running cleanup.

**Say:** “The course budget is 250 US dollars. The project's monthly ten-dollar alert is a notification, not a spending cap. Existing storage and hosting can continue to cost money. After the presentation, we must remove the project resources using the documented cleanup process.”

### 30 — Close (25 seconds)

Return to the succeeded Medium execution and immutable release link.

**Say:** “This completes the HI-Medium walkthrough: versioned inputs enter S3, Glue prepares and scores them, Athena produces corridor analysis, and SageMaker verifies and builds the release. Lambda publishes the result through S3 and CloudFront, giving investigators and treasury users two distinct business views.”

## Capture checklist

- Every job and object shown must belong to this execution or be explicitly identified as an input it reused.
- Opening configuration pages is inspection; do not describe it as provisioning resources.
- Record actual run parameters, not only job-definition defaults.
- Keep console navigation and meaningful selections in the footage. Static screenshot collections alone do not demonstrate the process.
- Keep AWS passwords, MFA, tokens, secret values, private state and notification addresses out of footage.
- Label the completed-execution inspection, skipped training, reused uploads, loading cuts and any historical failure.
- Read the script naturally; keep pauses for voiceover. Record actual chapter start/end times after capture, then supply the matching timestamped script.
- Review the exported recording for readable text and accidental private information before sharing. YouTube upload is a separate step.
