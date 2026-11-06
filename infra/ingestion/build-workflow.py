"""Generate the main batch workflow; no AWS calls.

Curate (Glue) -> parallel [Baseline (Glue) -> optional Train (SageMaker),
Score (Glue), Network (Glue)] -> Export (Glue) -> Publish (Lambda).
Glue .sync tasks retain their run IDs for completion checks. SageMaker polling
is used only for candidate training. Compute is never automatically retried;
failures preserve outputs and ask the control Lambda to stop owned workers.
Network is skipped on a cache hit, exactly like Curate/Score.
Regenerate with: python infra/ingestion/build-workflow.py
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
            ``baseline``, ``export``, ``publish``, ``training_failed``...).
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


def training(name, request, next_state, *, branch=False):
    """Start an optional SageMaker Training job and poll at most 300 times."""
    kind = 'Training'
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
        name + 'Failed': {'Type': 'Fail', 'Error': 'TrainingFailedOrDeadline', 'Cause': 'Training did not succeed within 300 checks; preserve outputs and stop owned workers.'},
    }
    if not branch:
        # Top-level job: API errors on create/describe go to the failure path.
        for key in [name, name + 'Status']: states[key]['Catch'] = CATCH
        # Replace the plain Fail with a control-Lambda call. The 'training_failed'
        # op deliberately raises an error, which this state's Catch turns into
        # $.failure and routes to RecordFailure (so the failure is recorded the
        # same way as every other failure).
        states[name + 'Failed'] = control('training_failed', None, 'RecordFailure', stage=name.lower())
    return states


def glue_stage(stage, request, next_state, *, branch=False):
    """Run a Glue stage once; retain its run ID for completion/provenance checks."""
    state = {'Type': 'Task', 'Resource': 'arn:aws:states:::glue:startJobRun.sync',
        'Parameters': {field + '.$': request + '.' + field for field in
                       ['JobName', 'Arguments', 'WorkerType', 'NumberOfWorkers', 'Timeout']},
        'ResultPath': '$.' + stage + '_job', 'Next': next_state}
    if not branch:
        state['Catch'] = CATCH
    return state


def workflow():
    """Return the complete ASL definition of the ``is459-g3-ingest`` workflow.

    The execution input must contain ``run_id`` (all control calls pass it to
    the Lambda). Intermediate results are kept in the execution state:
    ``$.work`` (cache flags and job arguments from ``begin``),
    ``$.baseline_request``, ``$.training``, ``$.export_request``
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
    # BQ2 mule networks: the Glue network job reads the curated Parquet and
    # writes the hub/loop/ring tables; the export stage recomputes and checks
    # them. Same cost controls as scoring and no Retry.
    network = {'Type': 'Task', 'Resource': 'arn:aws:states:::glue:startJobRun.sync',
               'Parameters': {'JobName': 'is459-g3-ingest-network', 'Arguments.$': '$.work.network_arguments', 'WorkerType': 'G.1X', 'NumberOfWorkers.$': '$.work.glue_workers', 'Timeout.$': '$.work.glue_timeout'}, 'ResultPath': None, 'End': True}
    # Branch 1 of the Parallel state: independent baseline verification, then
    # optional training. The dict union operator "|" (Python 3.9+) merges the
    # generated state dictionaries.
    verify_train = {'Baseline': glue_stage('baseline', '$.baseline_request', 'PrepareBranches', branch=True)} | {
        # Control Lambda checks the baseline proof and, if the run asked for
        # training, copies the training inputs and returns a training request
        # in $.training.training_request (None when no training is wanted).
        'PrepareBranches': control('baseline', '$.training', 'TrainRequested', branch=True, **{'job_result.$': '$.baseline_job'}),
        # Train only when the manifest requested it ($.work.train); otherwise
        # keep using the frozen model.
        'TrainRequested': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.train', 'BooleanEquals': True, 'Next': 'Train'}], 'Default': 'ReuseFrozen'},
        'TrainDone': {'Type': 'Pass', 'End': True}, 'ReuseFrozen': {'Type': 'Pass', 'End': True},
    } | training('Train', '$.training.training_request', 'TrainDone', branch=True)
    states = {
        # Validate the upload, take the pipeline lock, check stage caches and
        # prepare all job arguments; result stored in $.work.
        'Begin': control('begin', '$.work', 'ReuseCurated'),
        # Skip curation if an identical earlier run's curated output can be reused.
        'ReuseCurated': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.curated_cache_hit', 'BooleanEquals': True, 'Next': 'PrepareBaseline'}], 'Default': 'Curate'},
        # Glue curate job: raw CSV + FX -> curated Parquet (waits via .sync).
        'Curate': {'Type': 'Task', 'Resource': 'arn:aws:states:::glue:startJobRun.sync', 'Parameters': {'JobName': 'is459-g3-ingest-curate', 'Arguments.$': '$.work.curate_arguments', 'WorkerType': 'G.1X', 'NumberOfWorkers.$': '$.work.glue_workers', 'Timeout.$': '$.work.glue_timeout'}, 'ResultPath': None, 'Next': 'PrepareBaseline', 'Catch': CATCH},
        # Confirm curated output (_SUCCESS marker), register the run's Athena
        # table and build the baseline Glue request.
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
            {'StartAt': 'ReuseNetwork', 'States': {
                # Skip the network job if identical network tables were already produced
                # earlier; the export stage still recomputes and checks them.
                'ReuseNetwork': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.network_cache_hit', 'BooleanEquals': True, 'Next': 'NetworkReused'}], 'Default': 'Network'},
                'Network': network, 'NetworkReused': {'Type': 'Pass', 'End': True}}}], 'ResultPath': None, 'Next': 'PrepareExport', 'Catch': CATCH},
        # Verify scores, baseline proof, the network job's output and any
        # candidate model, then build the export Glue request.
        'PrepareExport': control('export', '$.export_request', 'Export'),
        # Export Glue job builds the dashboard release files.
        'Export': glue_stage('export', '$.export_request', 'Publish'),
        # Verify and publish the release to the dashboard; ResultPath '$'
        # replaces the state with the publish result, which becomes the output.
        'Publish': control('publish', '$', 'Complete', **{'job_result.$': '$.export_job'}), 'Complete': {'Type': 'Succeed'},
        # Failure path, step 1: write failure evidence and stop this run's
        # workers. If that call itself fails, still continue to notification.
        'RecordFailure': {'Type': 'Task', 'Resource': '${control_arn}', 'Parameters': {'op': 'failed', 'run_id.$': '$.run_id', 'failure.$': '$.failure'}, 'ResultPath': None, 'Next': 'NotifyFailure', 'Catch': [{'ErrorEquals': ['States.ALL'], 'ResultPath': None, 'Next': 'NotifyFailure'}]},
        # Step 2: e-mail the error details via SNS; never block on SNS errors.
        'NotifyFailure': {'Type': 'Task', 'Resource': 'arn:aws:states:::sns:publish', 'Parameters': {'TopicArn': '${failure_topic}', 'Message.$': 'States.JsonToString($.failure)'}, 'ResultPath': None, 'Next': 'Failed', 'Catch': [{'ErrorEquals': ['States.ALL'], 'ResultPath': None, 'Next': 'Failed'}]},
        # Step 3: end the execution as FAILED (outputs and lock are preserved).
        'Failed': {'Type': 'Fail', 'Error': 'IngestFailed', 'Cause': 'Run outputs preserved; inspect evidence and recover only after all workers stop.'},
    }
    return {'Comment': 'Parallel verified full batch; Glue stages and bounded training checks; no paid-job retries.', 'StartAt': 'Begin', 'TimeoutSeconds': 43200, 'States': states}


if __name__ == '__main__':
    # Write the definition next to this script (pretty-printed, newline at end).
    Path(__file__).with_name('workflow.asl.json').write_text(json.dumps(workflow(), indent=2) + '\n')
