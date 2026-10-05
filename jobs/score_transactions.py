"""Glue 5 frozen batch inference. No training, catalog writes or dashboard publishing."""
import argparse
import json
from pathlib import Path

from pyspark import SparkFiles, StorageLevel
from pyspark.sql import SparkSession, functions as F
from pyspark.sql.types import DoubleType, LongType, StringType, StructField, StructType, TimestampType, TimestampNTZType

from bda_pipeline.inference import SCORING_COLUMNS, score_frame, verify_artifacts, worker_ranker


def score(spark, input_path, output_path, model_dir, manifest_sha256, expected_rows):
    if expected_rows <= 0:
        raise ValueError('Expected row count must be positive')
    # For both S3 and local Hadoop filesystems, reject any existing destination.
    target = spark._jvm.org.apache.hadoop.fs.Path(output_path)
    filesystem = target.getFileSystem(spark._jsc.hadoopConfiguration())
    if filesystem.exists(target):
        raise ValueError('Use a fresh scoring output prefix')
    manifest = verify_artifacts(model_dir, manifest_sha256)
    worker_ranker(str(Path(model_dir).resolve()), manifest_sha256, 1)
    for name in [*manifest['files'], 'model-manifest.json']:
        spark.sparkContext.addFile(str((Path(model_dir) / name).resolve()))
    spark.conf.set('spark.sql.session.timeZone', 'UTC')
    spark.conf.set('spark.sql.execution.arrow.maxRecordsPerBatch', '10000')
    incoming = spark.read.parquet(input_path).select(*SCORING_COLUMNS)
    schema = incoming.schema
    for field in schema:
        if field.name in ['transaction_id', 'from_bank', 'from_account', 'to_bank', 'to_account',
                          'payment_format', 'payment_currency', 'receiving_currency']:
            if not isinstance(field.dataType, StringType):
                raise ValueError(f'Identifier/category must be string: {field.name}')
    if not isinstance(schema['source_row_index'].dataType, LongType) or not isinstance(schema['timestamp'].dataType, (TimestampType, TimestampNTZType)):
        raise ValueError('Expected long source index and timestamp')
    counts = incoming.agg(F.count('*').alias('rows'),
                         F.countDistinct('transaction_id').alias('ids'),
                         F.countDistinct('source_row_index').alias('indices')).first()
    if counts.rows != expected_rows or counts.ids != expected_rows or counts.indices != expected_rows:
        raise ValueError('Scoring input count or unique identity mismatch')
    result_schema = StructType([*schema.fields, StructField('model_score', DoubleType(), False)])

    def batches(iterator):
        directory = str(Path(SparkFiles.get('model-manifest.json')).parent)
        ranker = worker_ranker(directory, manifest_sha256, 1)
        for frame in iterator:
            yield score_frame(frame, ranker)

    scored = incoming.mapInPandas(batches, result_schema).persist(StorageLevel.DISK_ONLY)
    try:
        if scored.count() != expected_rows:
            raise ValueError('Scored output count mismatch')
        # Output carries both USD valuations but no labels. BQ2 remains unchanged.
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
    print(json.dumps(score(spark, args.INPUT, args.OUTPUT, args.MODEL_DIR,
                           args.MANIFEST_SHA256, args.EXPECTED_ROWS)), flush=True)
