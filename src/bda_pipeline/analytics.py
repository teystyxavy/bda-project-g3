"""Split by responsibility; re-exports the old names so existing imports keep working.

* ``bank_key``, ``add_behaviour`` -> ``bda_pipeline.features.behaviour``
* ``score_baseline``, ``evaluate_baseline`` -> ``bda_pipeline.baseline.rules``
* ``ranking_metrics`` -> ``bda_pipeline.common.metrics``
* ``corridor_totals``, ``concentration`` -> ``bda_pipeline.treasury.corridors``
"""
from .baseline.rules import evaluate_baseline, score_baseline
from .common.metrics import ranking_metrics
from .features.behaviour import add_behaviour, bank_key
from .treasury.corridors import concentration, corridor_totals

__moved_to__ = 'bda_pipeline.features.behaviour, baseline.rules, common.metrics, treasury.corridors'
