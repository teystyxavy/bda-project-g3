"""AWS Processing entry point. Data transfer is managed by SageMaker, not this worker."""
import sys
from bda_pipeline.ingest_compute import baseline_stage, export_stage


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ['baseline', 'export']:
        raise ValueError('Choose baseline or export')
    runner = baseline_stage if sys.argv[1] == 'baseline' else export_stage
    runner('/opt/ml/processing/input', '/opt/ml/processing/output', '/app/dashboard')


if __name__ == '__main__': main()
