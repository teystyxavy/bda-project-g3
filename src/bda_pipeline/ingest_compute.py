"""Moved to ``bda_pipeline.control.ingest_compute``; this alias keeps the old import path working."""
import sys

from .control import ingest_compute as _target

__moved_to__ = 'bda_pipeline.control.ingest_compute'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
