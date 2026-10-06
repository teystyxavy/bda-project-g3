"""Entry point for `docker run IMAGE train`, with no runtime package downloads."""
import json
import sys
from pathlib import Path

from bda_pipeline.sagemaker_training import train_channels


def main():
    if sys.argv[1:] not in ([], ['train']):
        raise ValueError('This image supports training only')
    root = Path('/opt/ml')
    try:
        parameters = json.loads((root / 'input/config/hyperparameters.json').read_text())
        train_channels(root / 'input/data/baseline', root / 'input/data/manifest',
                       root / 'output/work/candidate', root / 'model', root / 'output/data',
                       rounds=int(parameters['rounds']), threads=int(parameters['threads']),
                       expected_rows=int(parameters['expected_rows']))
    except Exception as error:
        (root / 'output').mkdir(parents=True, exist_ok=True)
        (root / 'output/failure').write_text(str(error)[:1024], encoding='utf-8')
        raise


if __name__ == '__main__':
    main()
