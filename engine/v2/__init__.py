"""
ZERO Engine v2.0 — macro factor graph, unified aggregation, market-structure
study, strategy lab, institutional research desks and MCP research bridge.

Design rules for everything in this package:

* **Import-safe.** No network, no Streamlit, no torch at import time.
* **Pure core.** The maths (``analyze``, ``backtest``, ``study``) takes plain
  dicts / DataFrames and returns plain dicts, so it is unit-testable offline.
* **Honest degradation.** Every fetch returns ``(value, source)`` and never
  raises. Missing inputs lower ``confidence``; they never fabricate a number.
* **Provenance.** Anything derived carries the source and timestamp that
  produced it, so the UI can show *why* the engine believes a level.
"""

from __future__ import annotations

SCHEMA_VERSION = "2.0"

__all__ = ["SCHEMA_VERSION"]
