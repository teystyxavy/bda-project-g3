"""Moved to ``bda_pipeline.dashboards.dashboard``; this alias keeps the old import path working."""
import sys

from .dashboards import dashboard as _target

__moved_to__ = 'bda_pipeline.dashboards.dashboard'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
