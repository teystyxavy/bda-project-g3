"""Generate bounded orchestration with independent branches and no compute retries."""
import json
from pathlib import Path

CATCH = [{'ErrorEquals': ['States.ALL'], 'ResultPath': '$.failure', 'Next': 'RecordFailure'}]
RETRY = [{'ErrorEquals': ['Lambda.ServiceException', 'Lambda.AWSLambdaException', 'Lambda.SdkClientException', 'Lambda.TooManyRequestsException'], 'IntervalSeconds': 3, 'MaxAttempts': 2, 'BackoffRate': 2}]
READ_RETRY = [{'ErrorEquals': ['States.TaskFailed'], 'IntervalSeconds': 3, 'MaxAttempts': 2, 'BackoffRate': 2}]


def control(op, result, next_state, *, branch=False, **extra):
    state = {'Type': 'Task', 'Resource': '${control_arn}', 'Parameters': {'op': op, 'run_id.$': '$.run_id', **extra},
             'ResultPath': result, 'Next': next_state, 'Retry': RETRY}
    if not branch: state['Catch'] = CATCH
    return state


def processing(name, request, next_state, *, branch=False, kind='Processing'):
    fields = ['ProcessingJobName', 'RoleArn', 'AppSpecification', 'ProcessingInputs', 'ProcessingOutputConfig', 'ProcessingResources', 'StoppingCondition', 'NetworkConfig', 'Tags']
    if kind == 'Training':
        fields = ['TrainingJobName', 'RoleArn', 'AlgorithmSpecification', 'InputDataConfig', 'OutputDataConfig', 'ResourceConfig', 'StoppingCondition', 'HyperParameters', 'EnableNetworkIsolation', 'EnableManagedSpotTraining', 'EnableInterContainerTrafficEncryption', 'ProfilerConfig', 'Tags']
    states = {
        name: {'Type': 'Task', 'Resource': 'arn:aws:states:::aws-sdk:sagemaker:create' + kind + 'Job',
               'Parameters': {field + '.$': request + '.' + field for field in fields},
               'ResultPath': None, 'Next': name + 'PollInit'},
        name + 'PollInit': {'Type': 'Pass', 'Result': {'attempt': 0}, 'ResultPath': '$.poll', 'Next': name + 'Wait'},
        name + 'Wait': {'Type': 'Wait', 'Seconds': 15, 'Next': name + 'Status'},
        name + 'Status': {'Type': 'Task', 'Resource': 'arn:aws:states:::aws-sdk:sagemaker:describe' + kind + 'Job',
               'Parameters': {kind + 'JobName.$': request + '.' + kind + 'JobName'},
               'ResultSelector': {'status.$': '$.' + kind + 'JobStatus'},
               'ResultPath': '$.job_status', 'Next': name + 'Complete', 'Retry': READ_RETRY},
        name + 'Complete': {'Type': 'Choice', 'Choices': [
            {'Variable': '$.job_status.status', 'StringEquals': 'Completed', 'Next': next_state},
            {'Or': [{'Variable': '$.job_status.status', 'StringEquals': status} for status in ['Failed', 'Stopped']], 'Next': name + 'Failed'},
            {'Variable': '$.poll.attempt', 'NumericGreaterThanEquals': 179, 'Next': name + 'Failed'}], 'Default': name + 'PollIncrement'},
        name + 'PollIncrement': {'Type': 'Pass', 'Parameters': {'attempt.$': 'States.MathAdd($.poll.attempt, 1)'}, 'ResultPath': '$.poll', 'Next': name + 'Wait'},
        name + 'Failed': {'Type': 'Fail', 'Error': 'ProcessingFailedOrDeadline', 'Cause': 'Processing did not succeed within 180 checks; preserve outputs and stop owned workers.'},
    }
    if not branch:
        for key in [name, name + 'Status']: states[key]['Catch'] = CATCH
        states[name + 'Failed'] = control('processing_failed', None, 'RecordFailure', stage=name.lower())
    return states


def workflow():
    score = {'Type': 'Task', 'Resource': 'arn:aws:states:::glue:startJobRun.sync',
             'Parameters': {'JobName': 'is459-g3-ingest-score', 'Arguments.$': '$.work.score_arguments'}, 'ResultPath': None, 'End': True}
    athena = {'Type': 'Task', 'Resource': 'arn:aws:states:::athena:startQueryExecution.sync',
              'Parameters': {'QueryString.$': '$.work.query', 'ClientRequestToken.$': '$.work.query_token', 'WorkGroup': 'is459-g3-analysis', 'QueryExecutionContext': {'Database': 'is459_g3_ingest'}}, 'ResultPath': None, 'End': True}
    verify_train = processing('Baseline', '$.baseline_request', 'PrepareBranches', branch=True) | {
        'PrepareBranches': control('baseline', '$.training', 'TrainRequested', branch=True),
        'TrainRequested': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.train', 'BooleanEquals': True, 'Next': 'Train'}], 'Default': 'ReuseFrozen'},
        'TrainDone': {'Type': 'Pass', 'End': True}, 'ReuseFrozen': {'Type': 'Pass', 'End': True},
    } | processing('Train', '$.training.training_request', 'TrainDone', branch=True, kind='Training')
    states = {
        'Begin': control('begin', '$.work', 'ReuseCurated'),
        'ReuseCurated': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.curated_cache_hit', 'BooleanEquals': True, 'Next': 'PrepareBaseline'}], 'Default': 'Curate'},
        'Curate': {'Type': 'Task', 'Resource': 'arn:aws:states:::glue:startJobRun.sync', 'Parameters': {'JobName': 'is459-g3-ingest-curate', 'Arguments.$': '$.work.curate_arguments'}, 'ResultPath': None, 'Next': 'PrepareBaseline', 'Catch': CATCH},
        'PrepareBaseline': control('curated', '$.baseline_request', 'ScoreAndAnalyze'),
        'ScoreAndAnalyze': {'Type': 'Parallel', 'Branches': [
            {'StartAt': 'Baseline', 'States': verify_train},
            {'StartAt': 'ReuseScores', 'States': {
                'ReuseScores': {'Type': 'Choice', 'Choices': [{'Variable': '$.work.scored_cache_hit', 'BooleanEquals': True, 'Next': 'ScoresReused'}], 'Default': 'Score'},
                'Score': score, 'ScoresReused': {'Type': 'Pass', 'End': True}}},
            {'StartAt': 'Corridors', 'States': {'Corridors': athena}}], 'ResultPath': None, 'Next': 'PrepareExport', 'Catch': CATCH},
        'PrepareExport': control('export', '$.export_request', 'Export', **{'work.$': '$.work'}),
        **processing('Export', '$.export_request', 'Publish'),
        'Publish': control('publish', '$', 'Complete'), 'Complete': {'Type': 'Succeed'},
        'RecordFailure': {'Type': 'Task', 'Resource': '${control_arn}', 'Parameters': {'op': 'failed', 'run_id.$': '$.run_id', 'failure.$': '$.failure'}, 'ResultPath': None, 'Next': 'NotifyFailure', 'Catch': [{'ErrorEquals': ['States.ALL'], 'ResultPath': None, 'Next': 'NotifyFailure'}]},
        'NotifyFailure': {'Type': 'Task', 'Resource': 'arn:aws:states:::sns:publish', 'Parameters': {'TopicArn': '${failure_topic}', 'Message.$': 'States.JsonToString($.failure)'}, 'ResultPath': None, 'Next': 'Failed', 'Catch': [{'ErrorEquals': ['States.ALL'], 'ResultPath': None, 'Next': 'Failed'}]},
        'Failed': {'Type': 'Fail', 'Error': 'IngestFailed', 'Cause': 'Run outputs preserved; inspect evidence and recover only after all workers stop.'},
    }
    return {'Comment': 'Parallel verified full batch; 15-second bounded processing checks; no paid-job retries.', 'StartAt': 'Begin', 'TimeoutSeconds': 7200, 'States': states}


if __name__ == '__main__':
    Path(__file__).with_name('workflow.asl.json').write_text(json.dumps(workflow(), indent=2) + '\n')
