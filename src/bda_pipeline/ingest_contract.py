"""Standard-library-only contracts shared by the client, Lambda and AWS worker.

What this file is
-----------------
The "contract" for one pipeline run: the fixed AWS names (account, region,
buckets, state machine, Glue database), the S3 layout of a run, the rules a run
manifest must satisfy, the reviewed Athena corridor SQL (business question 2),
and the Glue stage job request used for the baseline and export
stages.

Why it is separate
------------------
Several very different programs must agree on these rules byte for byte:

* ``scripts/ingestion/ingest.py`` on a laptop (prepares/uploads a run and starts
  the ``is459-g3-ingest`` Step Functions state machine);
* ``cloud_acquisition.py`` in the ``is459-g3-acquire`` Lambda (builds the same
  manifest in AWS instead of on a laptop);
* ``ingest_control.py`` in the ``is459-g3-ingest-control`` Lambda (re-validates
  the manifest at every step);
* ``stage_cache.py`` (cache keys).

Because it is imported by the small Lambda images, it deliberately uses only
the Python standard library (no pandas/boto3).

Reads/writes: nothing by itself. It only builds strings, dicts and hashes.

Key idea - content identity
---------------------------
Every run manifest records SHA-256 hashes of its input files and of the
deployed configuration. ``processing_sha256`` is a hash over the fields that
determine the output, so two runs over identical inputs share the same identity
and a previously published result can be reused instead of paying for compute
again (see ``pipeline/cache/<processing_sha256>.json``).
"""
import hashlib
import json
import re
from datetime import date

# ---- Fixed deployment coordinates (one AWS account, Singapore region) ----
ACCOUNT = '588738581042'
REGION = 'ap-southeast-1'
# Main data lake bucket: raw uploads, curated/scored Parquet, evidence, Athena results.
BUCKET = f'is459-g3-{ACCOUNT}-{REGION}'
# Separate bucket that CloudFront serves the static dashboard from.
SITE_BUCKET = f'is459-g3-dashboard-{ACCOUNT}-{REGION}'
# The batch Step Functions state machine; each execution is named after its run ID.
MACHINE = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:is459-g3-ingest'
# Glue Data Catalog database holding one Athena table per run (run_<id>).
DATABASE = 'is459_g3_ingest'
# S3 key of the deployed pipeline configuration (image URI, roles, model hash, ...).
CONFIG_KEY = 'pipeline/deployment.json'

# Resources by run size: the first profile whose max_rows covers the run is used (the last has no ceiling).
# The small profile and the medium Glue/processing values have run on AWS; the medium training values and the
# large profile are estimates to confirm on first use (also check the account's SageMaker quota for those instances).
RUN_PROFILES = [
    dict(name='small', max_rows=10_000_000, glue_workers=2, glue_timeout=10, shuffle_partitions=8,
         processing_instance='ml.m5.2xlarge', processing_volume_gb=30, processing_seconds=1800,
         train_instance='ml.m5.xlarge', train_threads=4, train_volume_gb=20,
         train_seconds=1200, train_max_seconds=1800),
    dict(name='medium', max_rows=50_000_000, glue_workers=4, glue_timeout=30, shuffle_partitions=32,
         processing_instance='ml.m5.2xlarge', processing_volume_gb=150, processing_seconds=3600,
         train_instance='ml.m5.4xlarge', train_threads=16, train_volume_gb=100,
         train_seconds=3600, train_max_seconds=7200),
    dict(name='large', max_rows=None, glue_workers=10, glue_timeout=120, shuffle_partitions=128,
         # The baseline/export Glue stages run single-threaded Python on one driver: the baseline stage took 379 s for 5.08M rows locally,
         # about 223 minutes linearly extrapolated to HI-Large's 179.7M, so the stage timeout (processing_seconds // 60) is 6 hours.
         processing_instance='ml.m5.4xlarge', processing_volume_gb=500, processing_seconds=21600,
         train_instance='ml.m5.12xlarge', train_threads=48, train_volume_gb=250,
         # Local scale check (5 candidates, 300 rounds): about 0.5 s per tree per million training rows, i.e. 3.6-5 hours for HI-Large's 108M; the
         # runtime limit is the profile cap because a job stopped at its limit is billed in full and produces nothing.
         train_seconds=21600, train_max_seconds=21600),
]
MAX_TRAIN_THREADS = max(profile['train_threads'] for profile in RUN_PROFILES)
TRAINING_ROLE = f'arn:aws:iam::{ACCOUNT}:role/is459-g3-sagemaker-training'


def valid_subset(subset):
    """Accepts any IBM subset name (``HI`` or ``LI`` with ``Small``, ``Medium`` or ``Large``); raises ValueError otherwise."""
    if not isinstance(subset, str) or not re.fullmatch(r'(HI|LI)-(Small|Medium|Large)', subset):
        raise ValueError('Unknown subset')
    return subset


def run_profile(rows):
    """Resource profile for a run of ``rows`` transactions; raises ValueError unless ``rows`` is a positive int."""
    if type(rows) is not int or rows <= 0:
        raise ValueError('Invalid run size')
    return next(profile for profile in RUN_PROFILES if profile['max_rows'] is None or rows <= profile['max_rows'])


def training_job_request(job_name, image_uri, rows, rounds=300, max_runtime=None):
    """The bounded ``CreateTrainingJob`` request for a run of ``rows`` transactions: the run profile's instance, volume and runtime, network isolation, no spot or profiler.
    Raises ValueError if rounds are outside 1-500 or the runtime is outside 60 seconds to the profile's cap."""
    profile = run_profile(rows)
    max_runtime = profile['train_seconds'] if max_runtime is None else max_runtime
    if not 1 <= rounds <= 500 or not 60 <= max_runtime <= profile['train_max_seconds']:
        raise ValueError('Allow 1-500 rounds and a runtime of 60 seconds up to the profile cap')
    prefix = f's3://{BUCKET}/sagemaker/inputs/{job_name}'
    return {
        'TrainingJobName': job_name, 'RoleArn': TRAINING_ROLE,
        'AlgorithmSpecification': {'TrainingImage': image_uri, 'TrainingInputMode': 'File'},
        'InputDataConfig': [
            {'ChannelName': channel, 'DataSource': {'S3DataSource': {
                'S3DataType': 'S3Prefix', 'S3Uri': f'{prefix}/{channel}/',
                'S3DataDistributionType': 'FullyReplicated'}}, 'InputMode': 'File'}
            for channel in ['baseline', 'manifest']],
        'OutputDataConfig': {'S3OutputPath': f's3://{BUCKET}/sagemaker/outputs'},
        'ResourceConfig': {'InstanceType': profile['train_instance'], 'InstanceCount': 1,
                           'VolumeSizeInGB': profile['train_volume_gb']},
        'StoppingCondition': {'MaxRuntimeInSeconds': max_runtime},
        'HyperParameters': {'rounds': str(rounds), 'threads': str(profile['train_threads']), 'expected_rows': str(rows)},
        'EnableNetworkIsolation': True, 'EnableInterContainerTrafficEncryption': True,
        'EnableManagedSpotTraining': False,
        'ProfilerConfig': {'DisableProfiler': True},
        'Tags': [{'Key': 'Project', 'Value': 'is459-g3'},
                 {'Key': 'Component', 'Value': 'SageMakerTraining'}],
    }



def canonical(value):
    """Serialise a JSON-compatible value to canonical UTF-8 bytes.

    Keys are sorted and whitespace removed, so the same logical document always
    produces exactly the same bytes (and therefore the same hash).
    ``allow_nan=False`` rejects NaN/Infinity, which are not valid JSON and would
    make hashes ambiguous.

    Args:
        value: Any JSON-serialisable value (dict, list, str, number, ...).

    Returns:
        ``bytes`` suitable for hashing or for an S3 ``put_object`` body.
    """
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(value):
    """Return the hex SHA-256 of ``canonical(value)``.

    Used everywhere as a stable fingerprint of a JSON document: the deployment
    config (``deployment_sha256``), the run identity (``processing_sha256``),
    stage-cache keys and verification receipts.
    """
    return hashlib.sha256(canonical(value)).hexdigest()


def valid_run(run_id):
    """Check that a run ID is safe to embed in S3 keys, job names and SQL.

    The run ID becomes part of S3 prefixes, SageMaker/Glue job names, the Step
    Functions execution name and an Athena table name, so it is restricted to
    3-36 characters of lowercase letters, digits and hyphens, starting with a
    letter or digit. This also prevents path traversal or SQL injection via
    the ID.

    Args:
        run_id: Candidate run identifier, e.g. ``'20261006-101500-ab12cd34'``.

    Returns:
        The same ``run_id`` (so the call can be used inline).

    Raises:
        ValueError: if the ID is not a string or does not match the pattern.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{2,35}', run_id):
        raise ValueError('Use a safe run ID of 3-36 lowercase letters, digits or hyphens')
    return run_id


def validate_dates(start, end):
    """Validate an inclusive ISO date range.

    Each run carries its observed start/end dates for exact curation, FX
    coverage and corridor reconciliation. Comparing ISO ``YYYY-MM-DD``
    strings works because they sort chronologically.

    Args:
        start: First transaction date, ``'YYYY-MM-DD'``.
        end: Last transaction date, ``'YYYY-MM-DD'``.

    Raises:
        ValueError: if a value is not a real calendar date or the range is reversed.
    """
    for value in [start, end]:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Require ISO calendar dates')
        # fromisoformat() additionally rejects impossible dates like 2022-09-31.
        date.fromisoformat(value)
    if start > end:
        raise ValueError('Date range is reversed')


def validate_export_dates(value, start, end):
    """Validate an optional ``export_dates`` selection (``None`` = full population, unchanged behavior).

    Restricts baseline/export/training to a chosen set of ``txn_date``
    partitions out of an otherwise fully-curated run; curate itself always
    processes the full raw population.

    Args:
        value: ``None``, or ``{'dates': [...ISO dates...], 'tail_start': 'YYYY-MM-DD' | None}``.
        start: The run's ``start_date`` (``export_dates`` must stay within range).
        end: The run's ``end_date``.

    Raises:
        ValueError: on any malformed or out-of-range selection.
    """
    if value is None:
        return
    if not isinstance(value, dict) or set(value) - {'dates', 'tail_start'}:
        raise ValueError('Invalid export_dates shape')
    dates = value.get('dates')
    if not isinstance(dates, list) or not dates:
        raise ValueError('export_dates.dates must be a non-empty list')
    if len(set(dates)) != len(dates):
        raise ValueError('export_dates.dates has duplicates')
    for day in dates:
        validate_dates(day, day)
        if day < start or day > end:
            raise ValueError('export_dates.dates outside the run\'s date range')
    tail_start = value.get('tail_start')
    if tail_start is not None and tail_start not in dates:
        raise ValueError('export_dates.tail_start must be one of export_dates.dates')


def validate_manifest(value, deployment_hash):
    """Validate a run manifest (``pipeline/runs/<run_id>/raw/manifest.json``).

    The manifest is written by the laptop launcher or the acquisition Lambda and
    then trusted by every later stage, so it is checked strictly each time it
    is read. Checks, in order:

    1. ``run_id`` is safe; schema version 1 and a known subset (``valid_subset``).
    2. ``rows`` is a positive int; the
       ``train`` and ``rerun`` flags are real booleans.
    3. ``deployment_sha256`` equals the hash of the currently deployed config,
       so a run prepared against an older deployment cannot be executed.
    4. Exactly the three allowed input files exist, each with a SHA-256 and a
       positive size.
    5. Dates form a valid, non-reversed range (``validate_dates``).
    6. The optional ``export_dates`` selection, if present, is well-formed and
       within range (``validate_export_dates``); absent means the full
       population, unchanged from before this field existed.
    7. ``processing_sha256`` is recomputed from the identity fields (which
       include ``export_dates``) and must match - this detects any tampering
       or accidental edits.

    Args:
        value: The parsed manifest dict.
        deployment_hash: ``sha(config)`` of the deployed ``pipeline/deployment.json``.

    Returns:
        The same manifest dict if valid.

    Raises:
        ValueError / KeyError: on any violation (the run then fails closed).
    """
    valid_run(value['run_id'])
    if value.get('schema_version') != 1:
        raise ValueError('Unsupported manifest schema')
    valid_subset(value.get('subset'))
    # ``type(...) is int`` (not isinstance) so that booleans, which are a
    # subclass of int in Python, are not accepted as a row count.
    if (type(value.get('rows')) is not int or value['rows'] <= 0
            or type(value.get('train')) is not bool or type(value.get('rerun')) is not bool):
        raise ValueError('Invalid run bounds or mode')
    if value.get('deployment_sha256') != deployment_hash:
        raise ValueError('Deployment changed; prepare again')
    if not re.fullmatch(r'[0-9a-f]{64}', value.get('processing_sha256', '')):
        raise ValueError('Invalid processing identity')
    # Allowlist: only these three prepared inputs may enter the pipeline.
    if set(value.get('files', {})) != {'transactions.csv', 'accounts.csv', 'fx.csv'}:
        raise ValueError('Invalid input allowlist')
    for info in value['files'].values():
        if (not re.fullmatch(r'[0-9a-f]{64}', info.get('sha256', ''))
                or type(info.get('bytes')) is not int or info['bytes'] <= 0):
            raise ValueError('Invalid source size/hash')
    validate_dates(value['start_date'], value['end_date'])
    validate_export_dates(value.get('export_dates'), value['start_date'], value['end_date'])
    # Recompute the run identity. Only fields that change the *output* are
    # included (not run_id, rerun or created_at), so identical inputs on a new
    # run ID share the same processing_sha256 and can hit the result cache.
    # export_dates is part of identity: a different selection is different output.
    identity = {key: value[key] for key in ['subset', 'rows', 'files', 'start_date', 'end_date',
                                           'dataset_version', 'deployment_sha256', 'train']}
    identity['export_dates'] = value.get('export_dates')
    if sha(identity) != value['processing_sha256']:
        raise ValueError('Processing identity mismatch')
    return value


def paths(run_id):
    """Return the S3 key prefixes (inside ``BUCKET``) for every stage of a run.

    Layout under ``pipeline/runs/<run_id>/``:

    * ``raw``      - uploaded inputs (transactions.csv, accounts.csv, fx.csv,
      manifest.json, upload-claim.json)
    * ``curated``  - Glue curate job output (Parquet partitioned by txn_date)
    * ``scored``   - Glue score job output (XGBoost model scores)
    * ``baseline`` - Glue baseline proof and rule-based snapshot
    * ``evidence`` - control-plane receipts (stage-cache.json, failure.json, result.json)
    * ``export``   - Glue export: verified dashboard + release manifest
    * ``release``  - reserved name for release artefacts

    Args:
        run_id: A run ID (validated).

    Returns:
        ``{stage_name: 'pipeline/runs/<run_id>/<stage_name>'}`` (no trailing slash).
    """
    valid_run(run_id)
    prefix = f'pipeline/runs/{run_id}'
    return {name: f'{prefix}/{name}' for name in ['raw', 'curated', 'scored', 'baseline',
                                                'evidence', 'export', 'release']}


def corridor_query(run_id, start, end):
    """Build the reviewed Athena SQL for business question 2 (treasury corridors).

    The query reads the run's curated table ``is459_g3_ingest.run_<run_id>``
    (registered by ``ingest_control`` with partition projection on ``txn_date``)
    and, for every day, produces two kinds of directed corridors:

    * ``bank``     - from_bank -> to_bank
    * ``currency`` - payment_currency -> receiving_currency

    For each corridor/day it reports the transaction count and USD volume, the
    corridor's share of that day's volume (``volume_share``), and the daily
    Herfindahl-Hirschman Index ``daily_hhi = sum(volume_share^2)`` - a standard
    concentration measure (close to 1 = flows concentrated in few corridors,
    close to 0 = spread out). ``nullif(..., 0)`` avoids division by zero.

    The same exact text is later compared with what Athena reports it ran, and
    the results are re-computed independently in pandas
    (``treasury_verification``) before release. Interpolating values into SQL
    is safe here only because ``run_id`` and the dates are strictly validated
    first.

    Args:
        run_id: Run ID; hyphens become underscores in the table name.
        start: Inclusive first date ``'YYYY-MM-DD'``.
        end: Inclusive last date ``'YYYY-MM-DD'``.

    Returns:
        The SQL string.
    """
    valid_run(run_id)
    validate_dates(start, end)
    # Athena/Glue table names cannot contain hyphens.
    table = 'run_' + run_id.replace('-', '_')
    return f"""WITH directed AS (
 SELECT 'bank' AS corridor_type, txn_date, from_bank AS from_node, to_bank AS to_node,
 count(*) AS transactions, sum(amount_usd) AS volume_usd FROM {DATABASE}.{table}
 WHERE txn_date BETWEEN '{start}' AND '{end}' GROUP BY txn_date, from_bank, to_bank
 UNION ALL
 SELECT 'currency', txn_date, payment_currency, receiving_currency, count(*), sum(amount_usd)
 FROM {DATABASE}.{table} WHERE txn_date BETWEEN '{start}' AND '{end}'
 GROUP BY txn_date, payment_currency, receiving_currency
), shares AS (SELECT *, volume_usd / nullif(sum(volume_usd) OVER
 (PARTITION BY corridor_type, txn_date), 0) AS volume_share FROM directed)
SELECT *, sum(volume_share*volume_share) OVER (PARTITION BY corridor_type, txn_date)
 AS daily_hhi FROM shares ORDER BY corridor_type, txn_date, volume_usd DESC, from_node, to_node"""


def stage_request(run_id, stage, config, extra_inputs=None, subset='HI-Small', rows=1):
    """Build a bounded Glue StartJobRun request for verification/baseline or export.

    Calculations run on the G.2X driver using the independently tested Python
    reference implementation. S3 channel layouts and output contracts are
    unchanged. SageMaker is reserved for optional training.
    """
    p = paths(run_id)
    valid_subset(subset)
    profile = run_profile(rows)
    if stage not in ['baseline', 'export']:
        raise ValueError('Unsupported Glue stage')
    sources = {'run': f"{p['raw']}/manifest.json", 'model': config['model_prefix'] + '/'}
    if stage == 'baseline':
        sources.update({'curated': p['curated'] + '/', 'raw': p['raw'] + '/'})
    else:
        sources.update({'baseline': p['baseline'] + '/baseline/bq1_scored.parquet',
                        'baseline_manifest': p['baseline'] + '/manifest/', 'scored': p['scored'] + '/'})
        extra = extra_inputs or {}
        if set(extra) - {'athena', 'candidate'}:
            raise ValueError('Unsupported export input')
        if 'athena' in extra and not re.fullmatch(r'athena-results/[A-Za-z0-9-]+\.csv', extra['athena']):
            raise ValueError('Unexpected Athena input')
        if 'candidate' in extra and extra['candidate'] != f'sagemaker/outputs/is459-g3-train-{run_id}/output/model.tar.gz':
            raise ValueError('Unexpected candidate input')
        sources.update(extra)
    return {'JobName': f'is459-g3-ingest-{stage}',
        'Arguments': {'--RUN_ID': run_id, '--STAGE': stage,
            '--INPUTS_JSON': json.dumps(sources, sort_keys=True),
            '--DEPLOYMENT_SHA256': sha(config),
            '--OUTPUT': f"s3://{BUCKET}/{p[stage]}/"},
        'WorkerType': 'G.2X', 'NumberOfWorkers': 2,
        'Timeout': max(60 if subset.endswith('Medium') else 30, profile['processing_seconds'] // 60)}
