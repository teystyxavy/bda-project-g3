"""Moved to ``bda_pipeline.control.stage_cache``; this alias keeps the old import path working."""
import sys

from .control import stage_cache as _target

__moved_to__ = 'bda_pipeline.control.stage_cache'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
