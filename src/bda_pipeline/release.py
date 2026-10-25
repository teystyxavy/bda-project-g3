"""Moved to ``bda_pipeline.control.release``; this alias keeps the old import path working."""
import sys

from .control import release as _target

__moved_to__ = 'bda_pipeline.control.release'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
