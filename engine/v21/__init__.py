"""
ZERO v2.1 — Suggestions · Trading · Research · Agentic AI · Integrations.

This package is deliberately *engine only*: no Streamlit, no UI imports, so every
piece is testable from a plain Python process. ``ui/v21_panel.py`` is the thin
presentation layer.

Design rules carried over from v2.0:

* **No invented data.** Anything without a live source returns
  ``{"available": False, "reason": ...}`` rather than a plausible number.
* **Every claim is labelled.** A suggestion always carries ``engine``,
  ``rationale`` and ``confidence`` so the panel can show where it came from.
* **Network work is opt-in.** Functions that hit the wire are only called from a
  button press or a background job, never at import time.
"""

from __future__ import annotations

__version__ = "2.1.0"

__all__ = [
    "indices",
    "gex",
    "signals",
    "integrations",
    "reports",
    "ecosystem",
]
