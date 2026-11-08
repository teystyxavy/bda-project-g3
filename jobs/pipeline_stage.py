"""Glue 5 entry point for independent baseline checks/rules and dashboard export."""
import json
import sys
import tempfile
from pathlib import Path

import boto3
from botocore.config import Config
from awsglue.utils import getResolvedOptions
from pyspark import SparkContext, SparkFiles

from bda_pipeline.glue_compute import run_stage
from bda_pipeline.ingest_contract import REGION


def main():
    # A traceback identifies the current Python operation even during a long
    # pandas merge. Enabled only for the isolated checkpointed recovery job.
    if '--NETWORK_CHECKPOINTS' in sys.argv:
        import faulthandler
        faulthandler.dump_traceback_later(300, repeat=True)
    args = getResolvedOptions(sys.argv, ['RUN_ID', 'STAGE', 'INPUTS_JSON', 'DEPLOYMENT_SHA256', 'OUTPUT'])
    SparkContext.getOrCreate()
    # Assets are individually localized by Glue --extra-files, avoiding archive
    # extraction and ensuring the reviewed shell is the one being exported.
    with tempfile.TemporaryDirectory(prefix='bda-stage-') as scratch:
        assets = Path(scratch) / 'assets'
        assets.mkdir()
        import shutil
        for name in ('index.html', 'dashboard.js', 'dashboard.css'):
            source = Path(name)
            if not source.is_file():
                source = Path(SparkFiles.get(name))
            if not source.is_file():
                raise ValueError(f'Missing localized dashboard asset: {name}')
            shutil.copyfile(source, assets / name)
        s3 = boto3.client('s3', region_name=REGION, config=Config(
            retries={'total_max_attempts': 4, 'mode': 'standard'},
            connect_timeout=10, read_timeout=120, max_pool_connections=48))
        run_stage(s3, args['RUN_ID'], args['STAGE'],
                  json.loads(args['INPUTS_JSON']), args['DEPLOYMENT_SHA256'], args['OUTPUT'],
                  assets, Path(scratch) / 'work', checkpoint_enabled='--NETWORK_CHECKPOINTS' in sys.argv
                  and getResolvedOptions(sys.argv, ['NETWORK_CHECKPOINTS'])['NETWORK_CHECKPOINTS'] == 'true',
                  recovery_id=getResolvedOptions(sys.argv, ['RECOVERY_ID'])['RECOVERY_ID']
                  if '--RECOVERY_ID' in sys.argv else None,
                  component_review='--OVERSIZED_COMPONENT_REVIEW' in sys.argv
                  and getResolvedOptions(sys.argv, ['OVERSIZED_COMPONENT_REVIEW'])['OVERSIZED_COMPONENT_REVIEW'] == 'true',
                  recovery_attempt=getResolvedOptions(sys.argv, ['RECOVERY_ATTEMPT'])['RECOVERY_ATTEMPT']
                  if '--RECOVERY_ATTEMPT' in sys.argv else None,
                  checkpoint_read_only='--CHECKPOINT_READ_ONLY' in sys.argv
                  and getResolvedOptions(sys.argv, ['CHECKPOINT_READ_ONLY'])['CHECKPOINT_READ_ONLY'] == 'true',
                  compatible_network_hash=getResolvedOptions(sys.argv, ['NETWORK_COMPATIBLE_CODE'])['NETWORK_COMPATIBLE_CODE']
                  if '--NETWORK_COMPATIBLE_CODE' in sys.argv else None)


if __name__ == '__main__':
    main()
