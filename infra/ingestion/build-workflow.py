"""Generate bounded orchestration with independent branches and no compute retries.

Purpose
-------
Writes ``infra/ingestion/workflow.asl.json``: the Amazon States Language (ASL)
definition of the main ingestion state machine ``is459-g3-ingest``. Writing
the large JSON by hand would be error-prone, because the same "start a
SageMaker job, then poll until it finishes" pattern is repeated three times
(baseline processing, training, export processing). This script builds those
states from small Python helpers instead.

Where it sits in the pipeline
-----------------------------
``infra/ingestion/main.tf`` loads the generated file with ``templatefile()``
and substitutes the two placeholders left in it:

* ``${control_arn}``   - ARN of the control Lambda ``is459-g3-ingest-control``
  (``src/bda_pipeline/ingest_control.py``), which does validation, locking,
  caching, request building and publishing (selected by the ``op`` field);
* ``${failure_topic}`` - ARN of the shared SNS topic ``is459-g3-failures``.

``scripts/ingestion/check-ingestion.py`` substitutes the same placeholders to
check the definition offline.

Workflow shape (see ``workflow()``)::

    Begin -> (curated cache hit?) -> Curate (Glue) -> PrepareBaseline
      -> ScoreAndAnalyze (Parallel):
           1. Baseline (SageMaker Processing) -> PrepareBranches
              -> optional Train (SageMaker Training)
           2. Score (Glue, skipped on a scored cache hit)
           3. Corridors (Athena BQ2 query)
      -> PrepareExport -> Export (SageMaker Processing) -> Publish -> Complete
    any failure -> RecordFailure -> NotifyFailure (SNS) -> Failed

Design rules encoded here ("bounded", "no compute retries"):

* Paid compute (Glue, SageMaker, Athena) is never retried automatically; a
  failure stops the run and keeps its outputs for inspection.
* Only cheap, idempotent calls are retried: Lambda service errors and the
  read-only ``Describe*Job`` polls.
* Polling is bounded: at most 300 checks, 15 s apart (about 75 minutes), and
  the whole execution has a 4-hour timeout.

Usage
-----
Run locally after changing the workflow, then commit the generated JSON::

    python infra/ingestion/build-workflow.py

It makes no AWS calls; it only writes ``workflow.asl.json`` next to itself.
"""
import json
from pathlib import Path

# Catch-all error handler used on top-level states: on ANY error ("States.ALL")
# keep the original input, store the error details under $.failure, and jump
# to RecordFailure so the failure is written as evidence and notified.
CATCH = [{'ErrorEquals': ['States.ALL'], 'ResultPath': '$.failure', 'Next': 'RecordFailure'}]
# Retry policy for control-Lambda calls: only transient Lambda service/throttling
# errors (not errors raised by our own code). Up to 2 more attempts, waiting
# 3 s then 6 s (BackoffRate 2 doubles the wait each time).
RETRY = [{'ErrorEquals': ['Lambda.ServiceException', 'Lambda.AWSLambdaException', 'Lambda.SdkClientException', 'Lambda.TooManyRequestsException'], 'IntervalSeconds': 3, 'MaxAttempts': 2, 'BackoffRate': 2}]
# Retry policy for read-only status polls (Describe*Job). They have no side
# effects, so retrying a failed call is safe and avoids failing a long run on
# one transient API error.
READ_RETRY = [{'ErrorEquals': ['States.TaskFailed'], 'IntervalSeconds': 3, 'MaxAttempts': 2, 'BackoffRate': 2}]


def control(op, result, next_state, *, branch=False, **extra):
    """Build a Task state that invokes the control Lambda with one operation.

    Args:
        op: Operation name the Lambda dispatches on (``begin``, ``curated``,
            ``baseline``, ``export``, ``publish``, ``processing_failed``...).
        result: JSONPath where the Lambda's return value is stored in the
            execution state (e.g. ``'$.work'``), or ``None`` to discard it and
            pass the input through unchanged.
        next_state: Name of the state to go to on success.
        branch: True when the state lives inside a Parallel branch. Branch
            states get no Catch: Step Functions does not allow a branch to jump
            to a state outside the branch, so their errors bubble up to the
            Parallel state's own Catch instead.
        **extra: Additional Lambda parameters. Keys ending in ``.$`` are
            JSONPath references resolved at run time (e.g. ``{'work.$': '$.work'}``).

    Returns:
        dict: The ASL Task state. Every call passes ``run_id`` from the
        execution input and uses the transient-error ``RETRY`` policy.
    """
    # '${control_arn}' is left as a literal placeholder for Terraform's
    # templatefile(); it is not Python string formatting.
    state = {'Type': 'Task', 'Resource': '${control_arn}', 'Parameters': {'op': op, 'run_id.$': '$.run_id', **extra},
             'ResultPath': result, 'Next': next_state, 'Retry': RETRY}
    # Top-level states route any error to the shared failure path.
    if not branch: state['Catch'] = CATCH
    return state


def processing(name, request, next_state, *, branch=False, kind='Processing'):
    """Build the states that run one SageMaker job and wait for it to finish.

    The job is started with the AWS SDK integration (``createProcessingJob`` /
    ``createTrainingJob``) and then polled in a bounded loop, instead of using
    the ``.sync`` integration, so the maximum wait is explicit and the
    workflow controls exactly when it gives up::

        <name> (create job) -> <name>PollInit (attempt = 0)
          -> <name>Wait (15 s) -> <name>Status (describe job)
          -> <name>Complete (Choice):
               Completed            -> next_state
               Failed / Stopped     -> <name>Failed
               attempt >= 179       -> <name>Failed (deadline reached)
               otherwise            -> <name>PollIncrement (attempt + 1) -> <name>Wait

    Args:
        name: Prefix for the generated state names (e.g. ``'Baseline'``).
        request: JSONPath of the full job request built by the control Lambda
            (e.g. ``'$.baseline_request'``); each API field is copied from it.
        next_state: State to go to once the job has ``Completed``.
        branch: True inside a Parallel branch (no Catch, and the failure state
            is a plain Fail so the error propagates to the Parallel state).
        kind: ``'Processing'`` or ``'Training'``; selects the SageMaker API
            names and the request fields to forward.

    Returns:
        dict: Mapping of state name -> ASL state, ready to merge into a
        ``States`` dictionary.
    """
    # Request fields forwarded to CreateProcessingJob. The control Lambda builds
    # the complete request, so the workflow only copies fields across.
    fields = ['ProcessingJobName', 'RoleArn', 'AppSpecification', 'ProcessingInputs', 'ProcessingOutputConfig', 'ProcessingResources', 'StoppingCondition', 'NetworkConfig', 'Tags']
    if kind == 'Training':
        # Request fields forwarded to CreateTrainingJob instead.
        fields = ['TrainingJobName', 'RoleArn', 'AlgorithmSpecification', 'InputDataConfig', 'OutputDataConfig', 'ResourceConfig', 'StoppingCondition', 'HyperParameters', 'EnableNetworkIsolation', 'EnableManagedSpotTraining', 'EnableInterContainerTrafficEncryption', 'ProfilerConfig', 'Tags']
    states = {
        # Start the job. 'Field.$': '$.request.Field' copies each field from the
        # request object; ResultPath None discards the API response so the
        # execution state is unchanged. No Retry: creating a paid job is never
        # repeated automatically.
        name: {'Type': 'Task', 'Resource': 'arn:aws:states:::aws-sdk:sagemaker:create' + kind + 'Job',
               'Parameters': {field + '.$': request + '.' + field for field in fields},
               'ResultPath': None, 'Next': name + 'PollInit'},
        # Reset the poll counter stored at $.poll.attempt.
        name + 'PollInit': {'Type': 'Pass', 'Result': {'attempt': 0}, 'ResultPath': '$.poll', 'Next': name + 'Wait'},
        # Pause 15 s between status checks (Wait states cost nothing extra).
        name + 'Wait': {'Type': 'Wait', 'Seconds': 15, 'Next': name + 'Status'},
        # Read the job status; ResultSelector keeps only the status string and
        # stores it at $.job_status.status. READ_RETRY covers transient errors.
        name + 'Status': {'Type': 'Task', 'Resource': 'arn:aws:states:::aws-sdk:sagemaker:describe' + kind + 'Job',
               'Parameters': {kind + 'JobName.$': request + '.' + kind + 'JobName'},
               'ResultSelector': {'status.$': '$.' + kind + 'JobStatus'},
               'ResultPath': '$.job_status', 'Next': name + 'Complete', 'Retry': READ_RETRY},
        # Decide what to do with the status (rules are checked in order).
        # Attempts 0..299 = 300 checks x 15 s, roughly a 75-minute deadline.
        name + 'Complete': {'Type': 'Choice', 'Choices': [
            {'Variable': '$.job_status.status', 'StringEquals': 'Completed', 'Next': next_state},
            {'Or': [{'Variable': '$.job_status.status', 'StringEquals': status} for status in ['Failed', 'Stopped']], 'Next': name + 'Failed'},
            {'Variable': '$.poll.attempt', 'NumericGreaterThanEquals': 299, 'Next': name + 'Failed'}], 'Default': name + 'PollIncrement'},
        # Still running (InProgress/Stopping): add 1 to the counter with the
        # States.MathAdd intrinsic function and loop back to Wait.
        name + 'PollIncrement': {'Type': 'Pass', 'Parameters': {'attempt.$': 'States.MathAdd($.poll.attempt, 1)'}, 'ResultPath': '$.poll', 'Next': name + 'Wait'},
        # Default failure: a Fail state. Used as-is inside Parallel branches,
        # where the error then reaches the Parallel state's Catch.
        name + 'Failed': {'Type': 'Fail', 'Error': 'ProcessingFailedOrDeadline', 'Cause': 'Processing did not succeed within 300 checks; preserve outputs and stop owned workers.'},
    }
    if not branch:
        # Top-level job: API errors on create/describe go to the failure path.
        for key in [name, name + 'Status']: states[key]['Catch'] = CATCH
        # Replace the plain Fail with a control-Lambda call. The 'processing_failed'
        # op deliberately raises an error, which this state's Catch turns into
        # $.failure and routes to RecordFailure (so the failure is recorded the
        # same way as every other failure).
        states[name + 'Failed'] = control('processing_failed', None, 'RecordFailure', stage=name.lower())
    return states


def workflow():
    """Return the complete ASL definition of the ``is459-g3-ingest`` workflow.

    The execution input must contain ``run_id`` (all control calls pass it to
    the Lambda). Intermediate results are kept in the execution state:
    ``$.work`` (cache flags, job arguments and the corridor query from
    ``begin``), ``$.baseline_request``, ``$.training``, ``$.export_request``
    and, after an error, ``$.failure``.

    Returns:
        dict: ASL document with ``StartAt``, a 7200-second (2-hour) overall
        ``TimeoutSeconds`` and all ``States``.
    """
    # BQ1 scoring: run the Glue scoring job with the per-run arguments that
    # 'begin' prepared. ".sync" makes Step Functions wait until the Glue run
    # ends (and fail if the run fails). No Retry: a paid job is not re-run.
    score = {'Type': 'Task', 'Resource': 'arn:aws:states:::glue:startJobRun.sync',
             'Parameters': {'JobName': 'is459-g3-ingest-score', 'Arguments.$': '$.work.score_arguments', 'WorkerType': 'G.1X', 'NumberOfWorkers.$': '$.work.glue_workers', 'Timeout.$': '$.work.glue_timeout'}, 'ResultPath': None, 'End': True}
    # BQ2 corridors: run the corridor SQL built by 'begin' in the cost-capped
    # workgroup against the per-run ingest database. ClientRequestToken makes
    # the start idempotent (the same token cannot start a second query), and
    # the control Lambda later checks the query used exactly this SQL.
    athena = {'Type': 'Task', 'Resource': 'arn:aws:states:::athena:startQueryExecution.sync',
              'Parameters': {'QueryString.$': '$.work.query', 'ClientRequestToken.$': '$.work.query_token', 'WorkGroup': 'is459-g3-analysis', 'QueryExecutionContext': {'Database': 'is459_g3_ingest'}}, 'ResultPath': None, 'End': True}
    # Branch 1 of the Parallel state: independent baseline verification, then
    # optional training. The dict union operator "|" (Python 3.9+) merges the
    # generated state dictionaries.
    verify_train = processing('Baseline', '$.baseline_request', 'PrepareBranches', branch=True) | {
        # Control Lambda checks the baseline proof and, if the run asked for
        # training, copies the training inputs and returns a training request
        # in $.training.training_request (None when no training is wanted).
        'PrepareBranches': control('baseline', '$.training', 'TrainRequested', branch=True),
        # Train only when the manifest requested it ($.work.train); otherwise
        # keep using the frozen model.
        'TrainRequested': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.train', 'BooleanEquals': True, 'Next': 'Train'}], 'Default': 'ReuseFrozen'},
        'TrainDone': {'Type': 'Pass', 'End': True}, 'ReuseFrozen': {'Type': 'Pass', 'End': True},
    } | processing('Train', '$.training.training_request', 'TrainDone', branch=True, kind='Training')
    states = {
        # Validate the upload, take the pipeline lock, check stage caches and
        # prepare all job arguments; result stored in $.work.
        'Begin': control('begin', '$.work', 'ReuseCurated'),
        # Skip curation if an identical earlier run's curated output can be reused.
        'ReuseCurated': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.curated_cache_hit', 'BooleanEquals': True, 'Next': 'PrepareBaseline'}], 'Default': 'Curate'},
        # Glue curate job: raw CSV + FX -> curated Parquet (waits via .sync).
        'Curate': {'Type': 'Task', 'Resource': 'arn:aws:states:::glue:startJobRun.sync', 'Parameters': {'JobName': 'is459-g3-ingest-curate', 'Arguments.$': '$.work.curate_arguments', 'WorkerType': 'G.1X', 'NumberOfWorkers.$': '$.work.glue_workers', 'Timeout.$': '$.work.glue_timeout'}, 'ResultPath': None, 'Next': 'PrepareBaseline', 'Catch': CATCH},
        # Confirm curated output (_SUCCESS marker), register the run's Athena
        # table and build the baseline Processing request.
        'PrepareBaseline': control('curated', '$.baseline_request', 'ScoreAndAnalyze'),
        # Run the three independent branches at the same time to shorten the
        # run. ResultPath None discards the branch outputs (results live in S3);
        # an error in any branch is caught once here.
        'ScoreAndAnalyze': {'Type': 'Parallel', 'Branches': [
            {'StartAt': 'Baseline', 'States': verify_train},
            {'StartAt': 'ReuseScores', 'States': {
                # Skip scoring if identical scores were already produced earlier.
                'ReuseScores': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.scored_cache_hit', 'BooleanEquals': True, 'Next': 'ScoresReused'}], 'Default': 'Score'},
                'Score': score, 'ScoresReused': {'Type': 'Pass', 'End': True}}},
            {'StartAt': 'Corridors', 'States': {'Corridors': athena}}], 'ResultPath': None, 'Next': 'PrepareExport', 'Catch': CATCH},
        # Verify scores, baseline proof, query and any candidate model, then
        # build the export Processing request ($.work is passed to the Lambda).
        'PrepareExport': control('export', '$.export_request', 'Export', **{'work.$': '$.work'}),
        # Export processing job builds the dashboard release files.
        **processing('Export', '$.export_request', 'Publish'),
        # Verify and publish the release to the dashboard; ResultPath '$'
        # replaces the state with the publish result, which becomes the output.
        'Publish': control('publish', '$', 'Complete'), 'Complete': {'Type': 'Succeed'},
        # Failure path, step 1: write failure evidence and stop this run's
        # workers. If that call itself fails, still continue to notification.
        'RecordFailure': {'Type': 'Task', 'Resource': '${control_arn}', 'Parameters': {'op': 'failed', 'run_id.$': '$.run_id', 'failure.$': '$.failure'}, 'ResultPath': None, 'Next': 'NotifyFailure', 'Catch': [{'ErrorEquals': ['States.ALL'], 'ResultPath': None, 'Next': 'NotifyFailure'}]},
        # Step 2: e-mail the error details via SNS; never block on SNS errors.
        'NotifyFailure': {'Type': 'Task', 'Resource': 'arn:aws:states:::sns:publish', 'Parameters': {'TopicArn': '${failure_topic}', 'Message.$': 'States.JsonToString($.failure)'}, 'ResultPath': None, 'Next': 'Failed', 'Catch': [{'ErrorEquals': ['States.ALL'], 'ResultPath': None, 'Next': 'Failed'}]},
        # Step 3: end the execution as FAILED (outputs and lock are preserved).
        'Failed': {'Type': 'Fail', 'Error': 'IngestFailed', 'Cause': 'Run outputs preserved; inspect evidence and recover only after all workers stop.'},
    }
    return {'Comment': 'Parallel verified full batch; 15-second bounded processing checks; no paid-job retries.', 'StartAt': 'Begin', 'TimeoutSeconds': 14400, 'States': states}


if __name__ == '__main__':
    # Write the definition next to this script (pretty-printed, newline at end).
    Path(__file__).with_name('workflow.asl.json').write_text(json.dumps(workflow(), indent=2) + '\n')
