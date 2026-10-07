"""Standard-library-only contracts shared by the client, Lambda and AWS worker.

What this file is
-----------------
The "contract" for one pipeline run: the fixed AWS names (account, region,
buckets, state machine, Glue database), the S3 layout of a run, the rules a run
manifest must satisfy, the reviewed Athena corridor SQL (business question 2),
and the SageMaker Processing job request used for the baseline and export
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
    """Validate an inclusive ISO date range against the reviewed data window.

    The HI-Small transactions cover 1-18 September 2022 and the frozen model
    and FX checks were reviewed for exactly that window, so anything outside it
    is rejected rather than silently producing an unreviewed result. Comparing
    ISO ``YYYY-MM-DD`` strings works because they sort chronologically.

    Args:
        start: First transaction date, ``'YYYY-MM-DD'``.
        end: Last transaction date, ``'YYYY-MM-DD'``.

    Raises:
        ValueError: if a value is not a real calendar date, or the range is
        reversed or outside 2022-09-01..2022-09-18.
    """
    for value in [start, end]:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError('Require ISO calendar dates')
        # fromisoformat() additionally rejects impossible dates like 2022-09-31.
        date.fromisoformat(value)
    if not ('2022-09-01' <= start <= end <= '2022-09-18'):
        raise ValueError('Date range outside the reviewed model contract')


def validate_manifest(value, deployment_hash):
    """Validate a run manifest (``pipeline/runs/<run_id>/raw/manifest.json``).

    The manifest is written by the laptop launcher or the acquisition Lambda and
    then trusted by every later stage, so it is checked strictly each time it
    is read. Checks, in order:

    1. ``run_id`` is safe; schema version 1 and subset ``HI-Small`` only.
    2. ``rows`` is an int in (0, 5,100,000] (HI-Small has ~5.08M rows); the
       ``train`` and ``rerun`` flags are real booleans.
    3. ``deployment_sha256`` equals the hash of the currently deployed config,
       so a run prepared against an older deployment cannot be executed.
    4. Exactly the three allowed input files exist, each with a SHA-256 and a
       size under 2 GB.
    5. Dates are inside the reviewed window.
    6. ``processing_sha256`` is recomputed from the identity fields and must
       match - this detects any tampering or accidental edits.

    Args:
        value: The parsed manifest dict.
        deployment_hash: ``sha(config)`` of the deployed ``pipeline/deployment.json``.

    Returns:
        The same manifest dict if valid.

    Raises:
        ValueError / KeyError: on any violation (the run then fails closed).
    """
    valid_run(value['run_id'])
    if value.get('schema_version') != 1 or value.get('subset') != 'HI-Small':
        raise ValueError('Only the reviewed HI-Small schema/model is currently supported')
    # ``type(...) is int`` (not isinstance) so that booleans, which are a
    # subclass of int in Python, are not accepted as a row count.
    if (type(value.get('rows')) is not int or not 0 < value['rows'] <= 5_100_000
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
                or type(info.get('bytes')) is not int or not 0 < info['bytes'] < 2_000_000_000):
            raise ValueError('Invalid source size/hash')
    validate_dates(value['start_date'], value['end_date'])
    # Recompute the run identity. Only fields that change the *output* are
    # included (not run_id, rerun or created_at), so identical inputs on a new
    # run ID share the same processing_sha256 and can hit the result cache.
    identity = {key: value[key] for key in ['subset', 'rows', 'files', 'start_date', 'end_date',
                                           'dataset_version', 'deployment_sha256', 'train']}
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
    * ``baseline`` - SageMaker Processing baseline proof and rule-based snapshot
    * ``evidence`` - control-plane receipts (stage-cache.json, failure.json, result.json)
    * ``export``   - SageMaker Processing export: verified dashboard + release manifest
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


def processing_request(run_id, stage, config, extra_inputs=None):
    """Build a SageMaker ``CreateProcessingJob`` request for ``baseline`` or ``export``.

    The control Lambda returns this dict to Step Functions, which passes it
    field by field to ``sagemaker:CreateProcessingJob`` and then polls the job
    every 15 seconds. Inside the container, ``/app/pipeline_worker.py <stage>``
    runs ``ingest_compute.baseline_stage`` or ``ingest_compute.export_stage``.

    Inputs (S3 prefixes copied by SageMaker into ``/opt/ml/processing/input/``):

    * always: ``run`` (the run manifest) and ``model`` (frozen model artefacts
      under ``config['model_prefix']``);
    * baseline: ``curated`` Parquet and ``raw`` CSVs, so the curated data can be
      checked against the original source;
    * export: the baseline scored Parquet + baseline manifest, the Glue
      ``scored`` output, plus ``extra_inputs`` from the control plane (the
      Athena result CSV and, when training, the candidate model archive).

    Output: ``/opt/ml/processing/output`` is uploaded at the end of the job to
    ``s3://BUCKET/pipeline/runs/<run_id>/<stage>/``.

    Safety settings: network isolation (the container cannot reach the internet
    or call AWS APIs; SageMaker does all data transfer), encrypted
    inter-container traffic, one ml.m5.2xlarge instance and a 30-minute hard
    stop so a stuck job cannot run up cost.

    Args:
        run_id: Run ID.
        stage: ``'baseline'`` or ``'export'``.
        config: Deployed configuration (needs ``model_prefix``,
            ``processing_role_arn``, ``image_uri``).
        extra_inputs: Optional ``{input_name: s3_key}`` additions for export.

    Returns:
        The request dict.

    Raises:
        ValueError: for any other stage name.
    """
    p = paths(run_id)
    if stage not in ['baseline', 'export']:
        raise ValueError('Unsupported processing stage')
    # input name -> S3 key (relative to BUCKET). A trailing '/' means "whole prefix".
    sources = {'run': f"{p['raw']}/manifest.json", 'model': config['model_prefix'] + '/'}
    if stage == 'baseline':
        sources.update({'curated': p['curated'] + '/', 'raw': p['raw'] + '/'})
    if stage == 'export':
        sources.update({'baseline': p['baseline'] + '/baseline/bq1_scored.parquet',
                        'baseline_manifest': p['baseline'] + '/manifest/', 'scored': p['scored'] + '/',
                        **(extra_inputs or {})})
    # Most inputs land in a folder named after the input; the two baseline
    # inputs are nested so the worker sees the same 'baseline/...' layout that
    # the baseline stage originally wrote.
    local_paths = {'baseline': 'baseline/baseline', 'baseline_manifest': 'baseline/manifest'}
    return {
        # Deterministic job name: one baseline/export job per run, and the
        # control plane can find (or stop) it later by name.
        'ProcessingJobName': f'is459-g3-{stage}-{run_id}',
        'RoleArn': config['processing_role_arn'],
        'AppSpecification': {'ImageUri': config['image_uri'],
                             'ContainerEntrypoint': ['python', '/app/pipeline_worker.py'],
                             'ContainerArguments': [stage]},
        'ProcessingInputs': [{'InputName': name, 'S3Input': {
            'S3Uri': f's3://{BUCKET}/{key}', 'LocalPath': '/opt/ml/processing/input/' + local_paths.get(name, name),
            'S3DataType': 'S3Prefix', 'S3InputMode': 'File', 'S3DataDistributionType': 'FullyReplicated'}}
            for name, key in sources.items()],
        # 'EndOfJob' uploads only once the worker exits successfully, so a
        # crashed job does not leave a half-written stage output.
        'ProcessingOutputConfig': {'Outputs': [{'OutputName': stage,
            'S3Output': {'S3Uri': f"s3://{BUCKET}/{p[stage]}/", 'LocalPath': '/opt/ml/processing/output',
                         'S3UploadMode': 'EndOfJob'}}]},
        'ProcessingResources': {'ClusterConfig': {'InstanceCount': 1,
            'InstanceType': 'ml.m5.2xlarge', 'VolumeSizeInGB': 30}},
        'StoppingCondition': {'MaxRuntimeInSeconds': 1800},
        'NetworkConfig': {'EnableNetworkIsolation': True, 'EnableInterContainerTrafficEncryption': True},
        # Cost-allocation tag so project spend can be tracked in Billing.
        'Tags': [{'Key': 'Project', 'Value': 'is459-g3'}],
    }
