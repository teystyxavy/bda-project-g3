"""Moved to ``bda_pipeline.treasury.treasury_verification``; this alias keeps the old import path working."""
import sys

from .treasury import treasury_verification as _target

__moved_to__ = 'bda_pipeline.treasury.treasury_verification'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
