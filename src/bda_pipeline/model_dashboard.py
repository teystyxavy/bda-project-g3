"""Moved to ``bda_pipeline.dashboards.model_dashboard``; this alias keeps the old import path working."""
import sys

from .dashboards import model_dashboard as _target

__moved_to__ = 'bda_pipeline.dashboards.model_dashboard'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
