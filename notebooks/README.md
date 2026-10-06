# Exploratory notebooks

[eda.ipynb](eda.ipynb) contains the original transaction-level exploration of
`HI-Small`. Its saved results describe the synthetic dataset; rolling behavioural
features are computed and checked by the pipeline separately.

Open the notebook with a Jupyter-capable editor and use an environment with
`pandas`, `numpy` and `matplotlib`. Acquire local data from the repository root:

```powershell
python scripts/ingestion/kaggle-download.py
```

The notebook finds `dataset/` by searching its working directory and ancestors,
so it works when launched from the repository root or this folder. Keep source
data in ignored `dataset/` and generated analysis artifacts in ignored `output/`.
Use [scripts/local/](../scripts/local/) for repeatable pipeline analysis commands.
