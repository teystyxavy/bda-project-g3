"""Moved to ``bda_pipeline.modeling.model``; this alias keeps the old import path working."""
import sys

from .modeling import model as _target

__moved_to__ = 'bda_pipeline.modeling.model'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
