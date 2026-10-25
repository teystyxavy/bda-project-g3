"""Moved to ``bda_pipeline.common.ingest_contract``; this alias keeps the old import path working."""
import sys

from .common import ingest_contract as _target

__moved_to__ = 'bda_pipeline.common.ingest_contract'
# Same module object under both names, so monkeypatching either path patches the real code.
sys.modules[__name__] = _target
