"""
Index universe + live quotes for the v2.1 panels.

The panel's top-right dropdown reads :func:`index_options`; every suggestion is
computed for :func:`quote` of the selected index. Quotes go through the existing
``data.live_index_service`` (NSE → BSE → yfinance chain with a 60 s cache), so
this module adds no new vendor code and no new failure modes.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# Canonical keys used everywhere in v2.1. Each entry lists every alias the
# loaders already understand so one lookup serves the whole app.
UNIVERSE: Dict[str, Dict[str, Any]] = {
    "NIFTY": {
        "label": "NIFTY 50",
        "nse": "NIFTY",
        "yf": "^NSEI",
        "aite": "NIFTY 50",
        "lot": 75,
        "aliases": ["NIFTY", "NIFTY 50", "NIFTY50", "^NSEI"],
    },
    "SENSEX": {
        "label": "SENSEX",
        "nse": "SENSEX",
        "yf": "^BSESN",
        "aite": "SENSEX",
        "lot": 20,
        "aliases": ["SENSEX", "BSE SENSEX", "^BSESN"],
    },
    "BANKNIFTY": {
        "label": "BANKNIFTY",
        "nse": "BANKNIFTY",
        "yf": "^NSEBANK",
        "aite": "BANKNIFTY",
        "lot": 35,
        "aliases": ["BANKNIFTY", "BANK NIFTY", "^NSEBANK"],
    },
}

DEFAULT_INDEX = "NIFTY"

_QUOTE_TTL = 20.0
_quote_cache: Dict[str, Dict[str, Any]] = {}


def canonical(name: str) -> str:
    """Map any alias ('NIFTY 50', '^NSEI', ...) to a canonical key."""
    if not name:
        return DEFAULT_INDEX
    raw = str(name).strip()
    if raw.upper() in UNIVERSE:
        return raw.upper()
    for key, meta in UNIVERSE.items():
        for alias in meta["aliases"]:
            if raw.upper() == alias.upper():
                return key
    return DEFAULT_INDEX


def index_options() -> List[str]:
    """Labels for the panel dropdown."""
    return [meta["label"] for meta in UNIVERSE.values()]


def label(key: str) -> str:
    return UNIVERSE[canonical(key)]["label"]


def _empty(key: str, reason: str) -> Dict[str, Any]:
    return {
        "index": key,
        "label": label(key),
        "available": False,
        "reason": reason,
        "last": None,
        "change": None,
        "change_pct": None,
        "source": None,
        "as_of": None,
        "age_s": None,
    }


def quote(name: str, force: bool = False) -> Dict[str, Any]:
    """
    Live (or last-good) quote for one index.

    Never raises: a dead network returns ``available: False`` with the reason,
    which the panel renders as an explicit "no quote" card instead of a fake
    number.
    """
    key = canonical(name)
    now = time.time()
    cached = _quote_cache.get(key)
    if cached and not force and (now - cached.get("_ts", 0)) < _QUOTE_TTL:
        out = {k: v for k, v in cached.items() if k != "_ts"}
        out["age_s"] = round(now - cached["_ts"], 1)
        return out

    try:
        from data.live_index_service import get_live_index_quote
    except Exception as exc:  # pragma: no cover - import guard
        return _empty(key, f"live_index_service unavailable: {exc}")

    nse_name = UNIVERSE[key]["nse"]
    try:
        raw = get_live_index_quote(nse_name) or {}
    except Exception as exc:  # noqa: BLE001
        return _empty(key, f"quote failed: {type(exc).__name__}: {exc}")

    last = raw.get("last") or raw.get("last_price") or raw.get("price") or raw.get("ltp")
    prev = raw.get("prev_close") or raw.get("previous_close") or raw.get("close")
    if last is None:
        return _empty(key, "vendor returned no last price")

    try:
        last_f = float(last)
        prev_f = float(prev) if prev is not None else None
        change = (last_f - prev_f) if prev_f else None
        change_pct = (change / prev_f * 100.0) if (change is not None and prev_f) else None
    except (TypeError, ValueError):
        return _empty(key, "vendor returned a non-numeric price")

    out = {
        "index": key,
        "label": UNIVERSE[key]["label"],
        "available": True,
        "reason": "",
        "last": round(last_f, 2),
        "change": round(change, 2) if change is not None else None,
        "change_pct": round(change_pct, 2) if change_pct is not None else None,
        "source": raw.get("source") or raw.get("provider") or "live_index_service",
        "as_of": raw.get("timestamp") or raw.get("as_of"),
        "age_s": 0.0,
        "raw_keys": sorted(raw.keys()),
    }
    _quote_cache[key] = {**out, "_ts": now}
    return out


def quotes(names: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    return {canonical(n): quote(n) for n in (names or list(UNIVERSE))}


def yf_ticker(name: str) -> str:
    return UNIVERSE[canonical(name)]["yf"]


def aite_symbol(name: str) -> str:
    return UNIVERSE[canonical(name)]["aite"]


def lot_size(name: str) -> int:
    return int(UNIVERSE[canonical(name)]["lot"])
