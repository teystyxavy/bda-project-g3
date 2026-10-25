"""Moved to ``bda_pipeline.scoring.cloud_scores``; this alias keeps the old import path working."""
import sys

from .scoring import cloud_scores as _target

__moved_to__ = 'bda_pipeline.scoring.cloud_scores'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
