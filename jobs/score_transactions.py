"""Glue 5 frozen batch inference. No training, catalog writes or dashboard publishing.

Where this sits in the pipeline
-------------------------------
AWS Glue PySpark job that applies the frozen XGBoost ranker (BQ1) to every
curated transaction::

    S3 curated Parquet (from jobs/curate_transactions.py)
      -> this job (model files shipped to every executor)
      -> S3 scored Parquet: SCORING_COLUMNS + model_score, partitioned by
         txn_date, plus a zero-byte _SUCCESS marker

Deployed as ``is459-g3-score-frozen`` (``infra/scoring``; started by the
Step Functions workflow in ``infra/orchestration``) and as
``is459-g3-ingest-score`` (``infra/ingestion``). The model files arrive via
Glue ``--extra-files`` and the ``bda_pipeline`` package via
``--extra-py-files``; Python wheels are installed offline (no PyPI access).

The output is later checked row by row by
``bda_pipeline/cloud_scores.verify_full_scores`` (locally) or
``ingest_compute.verify_scores`` (Glue baseline/export).

Arguments: ``--INPUT`` (curated Parquet URI), ``--OUTPUT`` (fresh scored
prefix), ``--MODEL_DIR`` (default ``.`` = files Glue already copied to the
workers), ``--MANIFEST_SHA256`` (pinned hash of ``model-manifest.json``) and
``--EXPECTED_ROWS``.

``model_score`` is a ranking score in [0, 1], not a calibrated probability.
"""
import argparse
import json
from pathlib import Path

from pyspark import SparkFiles, StorageLevel
from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import DoubleType, LongType, StringType, StructField, StructType, TimestampType, TimestampNTZType

from bda_pipeline.inference import SCORING_COLUMNS, resolve_model_directory, score_frame, verify_artifacts, worker_ranker


def score(spark, input_path, output_path, model_dir, manifest_sha256, expected_rows):
    """Score all curated rows with the frozen model and write Parquet.

    Args:
        spark: active SparkSession.
        input_path: curated Parquet location (S3 or local).
        output_path: destination; must not exist yet.
        model_dir: model folder, or ``'.'`` to use the files Glue distributed
            via ``--extra-files`` (found through ``SparkFiles``).
        manifest_sha256: expected SHA-256 of ``model-manifest.json``; every
            model file is checked against the manifest before use.
        expected_rows: exact number of rows the input and output must have.

    Returns:
        A small status dict (printed to the job log by ``__main__``).

    Raises:
        ValueError: if the output already exists, the model files do not
        match the manifest, the input schema has the wrong types, the
        row/ID counts differ from ``expected_rows`` or have duplicates, or
        the scored row count differs.
    """
    if expected_rows <= 0:
        raise ValueError('Expected row count must be positive')
    # For both S3 and local Hadoop filesystems, reject any existing destination.
    target = spark._jvm.org.apache.hadoop.fs.Path(output_path)
    filesystem = target.getFileSystem(spark._jsc.hadoopConfiguration())
    if filesystem.exists(target):
        raise ValueError('Use a fresh scoring output prefix')
    already_localized = str(model_dir) == '.'
    if already_localized:
        # In Glue, --extra-files are copied next to the job; SparkFiles.get
        # tells us the real local folder.
        model_dir = resolve_model_directory(model_dir, SparkFiles.get('model-manifest.json'))
    # Check every model file's hash against the pinned manifest hash.
    manifest = verify_artifacts(model_dir, manifest_sha256)
    # Load once on the driver too, so a broken model fails fast before any
    # executor work starts.
    worker_ranker(str(Path(model_dir).resolve()), manifest_sha256, 1)
    if not already_localized:
        # Local/spark-submit runs: ship the model files to executors ourselves.
        for name in [*manifest['files'], 'model-manifest.json']:
            spark.sparkContext.addFile(str((Path(model_dir) / name).resolve()))
    spark.conf.set('spark.sql.session.timeZone', 'UTC')
    # Rows per Arrow batch handed to mapInPandas: bounds executor memory.
    spark.conf.set('spark.sql.execution.arrow.maxRecordsPerBatch', '10000')
    # Select only the model's input columns. Labels (is_laundering) are not
    # read, so they cannot leak into the scored output.
    incoming = spark.read.parquet(input_path).select(*SCORING_COLUMNS)
    schema = incoming.schema
    # Type checks: IDs and categories must be strings (e.g. keep leading
    # zeros in bank codes), the row index a long and timestamp a timestamp.
    for field in schema:
        if field.name in ['transaction_id', 'from_bank', 'from_account', 'to_bank', 'to_account',
                          'payment_format', 'payment_currency', 'receiving_currency']:
            if not isinstance(field.dataType, StringType):
                raise ValueError(f'Identifier/category must be string: {field.name}')
    if not isinstance(schema['source_row_index'].dataType, LongType) or not isinstance(schema['timestamp'].dataType, (TimestampType, TimestampNTZType)):
        raise ValueError('Expected long source index and timestamp')
    # One aggregation pass: total rows, distinct IDs and distinct row
    # indices must all equal expected_rows (complete and no duplicates).
    counts = incoming.agg(F.count('*').alias('rows'),
                         F.countDistinct('transaction_id').alias('ids'),
                         F.countDistinct('source_row_index').alias('indices')).first()
    if counts.rows != expected_rows or counts.ids != expected_rows or counts.indices != expected_rows:
        raise ValueError('Scoring input count or unique identity mismatch')
    result_schema = StructType([*schema.fields, StructField('model_score', DoubleType(), False)])

    def batches(iterator):
        """Executor-side function for mapInPandas: score each pandas batch.

        ``worker_ranker`` is cached per Python worker, so the model is loaded
        and verified once per worker, not once per batch. One thread per
        model because Spark already runs tasks in parallel.
        """
        directory = str(Path(SparkFiles.get('model-manifest.json')).parent)
        ranker = worker_ranker(directory, manifest_sha256, 1)
        for frame in iterator:
            yield score_frame(frame, ranker)

    # DISK_ONLY persist: scoring runs once, then the count and the write
    # reuse the result without re-scoring and without filling executor memory.
    scored = incoming.mapInPandas(batches, result_schema).persist(StorageLevel.DISK_ONLY)
    try:
        if scored.count() != expected_rows:
            raise ValueError('Scored output count mismatch')
        # Output carries both USD valuations but no labels. BQ2 remains unchanged.
        # errorifexists: never overwrite; partitioned by day like the input.
        scored.withColumn('txn_date', F.to_date('timestamp')).write.mode('errorifexists').partitionBy('txn_date').parquet(output_path)
    finally:
        scored.unpersist()
    return dict(rows=expected_rows, manifest_sha256=manifest_sha256,
                status='scored-not-published', score_definition='Ranking score, not calibrated probability')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--INPUT', required=True)
    parser.add_argument('--OUTPUT', required=True)
    parser.add_argument('--MODEL_DIR', default='.')
    parser.add_argument('--MANIFEST_SHA256', required=True)
    parser.add_argument('--EXPECTED_ROWS', required=True, type=int)
    # Glue supplies unrelated system flags. All own arguments remain required.
    args, _ = parser.parse_known_args()
    spark = SparkSession.builder.appName('is459-frozen-model-scoring').getOrCreate()
    # Print the status dict as one JSON line in the Glue job log.
    print(json.dumps(score(spark, args.INPUT, args.OUTPUT, args.MODEL_DIR,
                           args.MANIFEST_SHA256, args.EXPECTED_ROWS)), flush=True)
