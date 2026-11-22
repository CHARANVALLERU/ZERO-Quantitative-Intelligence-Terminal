"""
ZERO Engine v2.0 — Daily Session Brief
=====================================

When the admin opens the terminal, v2.0 reports what changed since the last
session: strategies created, how many passed, and the top three improvable
winners — with a one-click jump to the strategies folder.

The brief is *idempotent per day*: ``needs_brief()`` is true only when the
stored date differs from today, and ``mark_briefed()`` records the delivery.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

from engine.v2 import strategy_lab

SCHEMA_VERSION = "2.0"
STATE_PATH = os.path.join("db", "v2", "session_brief.json")


def _read_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(payload: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, allow_nan=False, default=str)
        os.replace(tmp, STATE_PATH)
    except (OSError, ValueError, TypeError):
        pass


def needs_brief(today: Optional[str] = None) -> bool:
    today = today or _dt.date.today().isoformat()
    return _read_state().get("last_brief_date") != today


def mark_briefed(today: Optional[str] = None) -> None:
    today = today or _dt.date.today().isoformat()
    state = _read_state()
    state["last_brief_date"] = today
    state["last_brief_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    _write_state(state)


def build() -> Dict[str, Any]:
    """Assemble the brief from the registry and the integrity audit."""
    registry = strategy_lab.load_registry()
    strategies = registry.get("strategies") or []
    passed = [s for s in strategies if s.get("status") == "PASSED"]
    validated = [s for s in strategies if s.get("validated")]
    top = registry.get("top") or []

    audit: Dict[str, Any] = {}
    try:
        from engine.v2.state_audit import audit as run_audit
        audit = run_audit()
    except Exception:
        pass

    macro: Dict[str, Any] = {}
    try:
        from engine.v2 import macro_factors
        snap = macro_factors.load_last_snapshot()
        if snap is not None:
            macro = macro_factors.analyze(snap, None)
    except Exception:
        pass

    return {
        "schema": SCHEMA_VERSION,
        "date": _dt.date.today().isoformat(),
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "strategies_created": registry.get("total", len(strategies)),
        "strategies_passed": registry.get("passed", len(passed)),
        "strategies_failed": registry.get("failed", len(strategies) - len(passed)),
        "validated": registry.get("validated", len(validated)),
        "top": [
            {
                "name": s.get("name"),
                "index": s.get("index"),
                "family": s.get("family"),
                "profit_factor": (s.get("metrics") or {}).get("profit_factor"),
                "return_pct": (s.get("metrics") or {}).get("return_pct"),
                "trades": (s.get("metrics") or {}).get("trades"),
            }
            for s in top
        ],
        "registry_updated_at": registry.get("updated_at"),
        "health": {
            "pct": audit.get("health_pct"),
            "failed": audit.get("failed"),
            "high": audit.get("high"),
        },
        "macro_regime": macro.get("regime"),
        "strategies_dir": os.path.abspath(strategy_lab.STRATEGY_DIR),
    }


def summary_line(brief: Optional[Dict[str, Any]] = None) -> str:
    """One-line text for the toast body."""
    b = brief or build()
    top = b.get("top") or []
    top_txt = ", ".join(
        f"{t['name']} (PF {t.get('profit_factor')})" for t in top if t.get("name")
    ) or "none yet"
    return (
        f"{b['strategies_created']} strategies · {b['strategies_passed']} passed · "
        f"{b.get('validated', 0)} validated · top: {top_txt}"
    )


def toast_payload() -> Dict[str, Any]:
    """Data the UI needs to render the brief + the folder jump button."""
    brief = build()
    return {
        **brief,
        "summary": summary_line(brief),
        "show": needs_brief(),
    }
