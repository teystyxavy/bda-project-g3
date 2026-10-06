"""Copy a hash-checked Lambda build context locally; no image push or AWS calls."""
import argparse,json,shutil
from pathlib import Path
from bda_pipeline.inference import verify_artifacts
from bda_pipeline.model import digest
from bda_pipeline.orchestration import MANIFEST


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Use a fresh build directory')
    verify_artifacts(args.artifacts,MANIFEST)
    root=Path(__file__).resolve().parents[2]
    args.output.mkdir(parents=True)
    for name in ['Dockerfile','requirements.txt','handler.py']:shutil.copyfile(root/'streaming'/name,args.output/name)
    shutil.copytree(root/'src/bda_pipeline',args.output/'bda_pipeline',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(args.artifacts,args.output/'artifacts')
    report={'status':'local-context-not-uploaded','manifest_sha256':MANIFEST,
            'files':{str(p.relative_to(args.output)):digest(p) for p in sorted(args.output.rglob('*')) if p.is_file()}}
    (args.output/'build-context.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':report['status'],'files':len(report['files'])}))


if __name__=='__main__':main()
