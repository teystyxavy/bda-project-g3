"""Reusable local logic for the Group 3 AML data pipeline.

``bda_pipeline`` is the shared Python package behind the IS459 Group 3 project
(IBM Anti-Money-Laundering transactions on AWS). The same modules are used in
several places:

* on a laptop by the ``scripts/`` command-line tools (download, prepare, smoke
  tests, verification);
* inside the AWS Lambda images (``cloud_acquisition`` for Kaggle + FX
  acquisition, ``ingest_control`` for the Step Functions control plane);
* inside the Glue container (``ingest_compute`` via
  ``jobs/pipeline_stage.py``) for baseline verification and release export.

Importing the package itself only re-exports the small "core transformation"
helpers that local scripts and tests use most often:

* ``build_daily_rates`` / ``discover_requirements`` (from ``fx``): find which
  dates and currencies the transactions need and fetch historical USD rates.
* ``read_transactions`` / ``read_accounts`` / ``build_curated_transactions``
  (from ``transform``): the pandas mirror of the Glue curate job that turns the
  raw Kaggle CSVs into curated, USD-normalised transactions.

Nothing here touches AWS or the network at import time.
"""

# Re-export the most commonly used local helpers so callers can write
# ``from bda_pipeline import build_curated_transactions`` instead of reaching
# into the individual submodules.
from .fx import build_daily_rates, discover_requirements
from .transform import build_curated_transactions, read_accounts, read_transactions

# ``__all__`` defines the public surface of ``from bda_pipeline import *``.
__all__ = [
    "build_curated_transactions",
    "build_daily_rates",
    "discover_requirements",
    "read_accounts",
    "read_transactions",
]
