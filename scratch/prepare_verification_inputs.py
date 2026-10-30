import hashlib, json, shutil
from pathlib import Path
import pandas as pd

raw = Path("output/verify-full/raw")
raw.mkdir(parents=True, exist_ok=True)
shutil.copyfile("dataset/HI-Small_Trans.csv", raw / "transactions.csv")
shutil.copyfile("dataset/HI-Small_accounts.csv", raw / "accounts.csv")
shutil.copyfile("scratch/fx_cloud.csv", raw / "fx.csv")  # must match what the cloud curation job actually used

def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

files = {name: {"sha256": digest(raw / name), "bytes": (raw / name).stat().st_size}
         for name in ["transactions.csv", "accounts.csv", "fx.csv"]}
rates = pd.read_csv(raw / "fx.csv")
manifest = {"files": files, "rows": 5078345, "start_date": "2022-09-01", "end_date": "2022-09-18",
            "currencies": sorted(rates.currency.unique().tolist())}
Path("output/verify-full/manifest.json").write_text(json.dumps(manifest, indent=2))
print("done")
