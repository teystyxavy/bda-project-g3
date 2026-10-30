import json
import sys
from pathlib import Path
sys.path.insert(0, "scratch")
from verify_curated_patched import verify_curated_skip_behaviour_resample
from bda_pipeline.batch import export_verified_batch

manifest = json.loads(Path("output/verify-full/manifest.json").read_text())
proof = verify_curated_skip_behaviour_resample("output/curated-full/curated", "output/verify-full/raw", manifest)
assert proof["status"] == "passed"
Path("output/verify-full/proof.json").write_text(json.dumps(proof))
report = export_verified_batch("output/curated-full/curated", "output/verify-full/proof.json",
                               "output/verify-full/export", "dashboard")
print(json.dumps(report["metrics"], indent=2))
