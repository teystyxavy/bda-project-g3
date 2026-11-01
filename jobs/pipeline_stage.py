"""Glue 5 entry point for independent baseline checks/rules and dashboard export."""
import json
import sys
import tempfile
from pathlib import Path

import boto3
from awsglue.utils import getResolvedOptions
from pyspark import SparkContext, SparkFiles

from bda_pipeline.glue_compute import run_stage
from bda_pipeline.ingest_contract import REGION


def main():
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
        run_stage(boto3.client('s3', region_name=REGION), args['RUN_ID'], args['STAGE'],
                  json.loads(args['INPUTS_JSON']), args['DEPLOYMENT_SHA256'], args['OUTPUT'],
                  assets, Path(scratch) / 'work')


if __name__ == '__main__':
    main()
