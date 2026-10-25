"""Moved to ``bda_pipeline.ingestion.transform``; this alias keeps the old import path working."""
import sys

from .ingestion import transform as _target

__moved_to__ = 'bda_pipeline.ingestion.transform'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
