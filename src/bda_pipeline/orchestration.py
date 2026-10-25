"""Moved to ``bda_pipeline.control.orchestration``; this alias keeps the old import path working."""
import sys

from .control import orchestration as _target

__moved_to__ = 'bda_pipeline.control.orchestration'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
