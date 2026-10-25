"""Moved to ``bda_pipeline.ingestion.acquisition``; this alias keeps the old import path working."""
import sys

from .ingestion import acquisition as _target

__moved_to__ = 'bda_pipeline.ingestion.acquisition'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
