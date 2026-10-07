"""Copy a hash-checked Lambda build context locally; no image push or AWS calls.

Purpose
-------
Assembles the Docker build context for the streaming Lambda container image
(the consumer in Kinesis -> Lambda -> DynamoDB). The image must contain the
*frozen* XGBoost model release, so this script first verifies the model
artifacts against the trusted manifest hash (``MANIFEST`` in
``bda_pipeline.orchestration``) before copying anything.

Resulting ``--output`` folder (fresh directory)::

    Dockerfile, requirements.txt, handler.py   <- from <repo>/streaming/
    bda_pipeline/                              <- shared package from src/
    artifacts/                                 <- verified model release
    build-context.json                         <- SHA-256 of every file

``build-context.json`` lets the team prove later that the image that was
built and pushed came from exactly these files. Building and pushing the
image to ECR is a separate, manually approved step.

Usage::

    python scripts/streaming/prepare-streaming-container.py \\
        --artifacts <model release dir> --output output/streaming-build
"""
import argparse,json,shutil
from pathlib import Path
from bda_pipeline.inference import verify_artifacts
from bda_pipeline.model import digest
from bda_pipeline.orchestration import MANIFEST


def main():
    """Verify the model artifacts, copy the build context and hash it.

    Raises:
        ValueError: if ``--output`` exists or any artifact/manifest hash differs.
    """
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    # A fresh folder guarantees no stale files end up inside the image.
    if args.output.exists():raise ValueError('Use a fresh build directory')
    # Checks model-manifest.json against the trusted SHA-256 and then every
    # allow-listed file (model .ubj, features/selection/comparison JSON).
    verify_artifacts(args.artifacts,MANIFEST)
    # Repository root (this file is in <root>/scripts/streaming/).
    root=Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True)
    # Lambda container files: Dockerfile (AWS Lambda Python base image pinned
    # by digest), Python requirements and the handler entry point.
    for name in ['Dockerfile','requirements.txt','handler.py']:shutil.copyfile(root/'streaming'/name,args.output/name)
    # Shared pipeline code, so the Lambda uses the same feature/score logic as batch.
    shutil.copytree(root/'src/bda_pipeline',args.output/'bda_pipeline',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(args.artifacts,args.output/'artifacts')
    # Record a SHA-256 for every file in the context (sorted for a stable report).
    report={'status':'local-context-not-uploaded','manifest_sha256':MANIFEST,
            'files':{str(p.relative_to(args.output)):digest(p) for p in sorted(args.output.rglob('*')) if p.is_file()}}
    (args.output/'build-context.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'files':len(report['files'])}))


if __name__=='__main__':main()
