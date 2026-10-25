"""Moved to ``bda_pipeline.scoring.inference``; this alias keeps the old import path working."""
import sys

from .scoring import inference as _target

__moved_to__ = 'bda_pipeline.scoring.inference'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
