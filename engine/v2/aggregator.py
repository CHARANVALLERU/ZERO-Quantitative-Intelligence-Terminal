"""
ZERO Engine v2.0 — Unified Multi-Source Aggregation
===================================================

Rule 11 of the v2.0 brief: *"use aggregated data of multiple financial data,
capital from multiple separate sources to single unified views."*

This module is the aggregation layer. It owns:

* the **universe registry** (NSE + BSE symbols the desk asked for),
* the **source registry** (exchange quotes, corporate releases, government
  releases, institutional / prop-desk flow, insider filings),
* the **merge contract** — one row per symbol with every source's value,
  timestamp and staleness, plus a single consensus ``composite_score``.

Nothing here invents an observation. If a source is unavailable the row keeps
``None`` and the coverage drops; the UI shows which sources are missing.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "2.0"

CACHE_PATH = os.path.join("db", "v2", "quotes_cache.json")
SIGNALS_PATH = os.path.join("db", "v2", "institutional_signals.json")
CACHE_TTL_SECONDS = 60

# ── Universe registry ───────────────────────────────────────────────────────
# NSE cash symbols use the ``-EQ`` series suffix as the desk wrote them; the
# Yahoo symbol is the bare ticker + ``.NS``. BSE uses ``.BO``.
NSE_UNIVERSE: List[str] = [
    "SBIN-EQ", "IIFL-EQ", "RELIANCE-EQ", "HDFCBANK-EQ", "ICICIBANK-EQ",
    "BSE-EQ", "AZAD-EQ", "V2RETAIL-EQ", "KOTAKBANK-EQ",
]
BSE_UNIVERSE: List[str] = [
    "SBIN", "RELIANCE", "ETERNAL", "ITC", "ICICIBANK",
    "AXISBANK", "BAJFINANCE", "HDFCBANK", "NTPC",
]

# Corporate / institutional sources the desk named. Each entry is a logical
# feed; concrete files live in db/v2/institutional_signals.json so they can be
# updated by a scraper, a human, or an MCP research call without code changes.
SOURCE_REGISTRY: List[Dict[str, str]] = [
    {"id": "nse_quote", "label": "NSE cash quote", "kind": "market", "cadence": "realtime"},
    {"id": "bse_quote", "label": "BSE cash quote", "kind": "market", "cadence": "realtime"},
    {"id": "govt_release", "label": "Government / MoSPI / PIB releases", "kind": "macro", "cadence": "event"},
    {"id": "corporate_release", "label": "Corporate results (TCS, Wipro, Cognizant, Accenture…)", "kind": "fundamental", "cadence": "event"},
    {"id": "institutional_flow", "label": "Institutional / prop-desk flow", "kind": "flow", "cadence": "daily"},
    {"id": "insider_filing", "label": "Insider / promoter filings", "kind": "flow", "cadence": "daily"},
    {"id": "mcp_research", "label": "MCP research bridge (REA + others)", "kind": "research", "cadence": "on-demand"},
]

_INDEX_PROXIES = {"NIFTY 50": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN"}


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path: str, payload: Any) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, allow_nan=False, default=str)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError):
        return False


def yahoo_symbol(symbol: str, exchange: str) -> str:
    """Map a desk symbol to its Yahoo ticker.

    ``SBIN-EQ`` on NSE -> ``SBIN.NS``; ``SBIN`` on BSE -> ``SBIN.BO``.
    Symbols that already carry a suffix are returned unchanged.
    """
    sym = str(symbol).strip().upper()
    if sym.endswith((".NS", ".BO")):
        return sym
    if sym.endswith("-EQ"):
        sym = sym[:-3]
    if sym.endswith("-BE") or sym.endswith("-BZ"):
        sym = sym[:-3]
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    return f"{sym}{suffix}"


def _flatten_ohlcv(frame) -> Optional[Any]:
    """Normalize a yfinance frame to single-level OHLCV columns."""
    if frame is None or getattr(frame, "empty", True):
        return None
    try:
        import pandas as pd  # local import: aggregator is import-safe
        out = frame.copy()
        if isinstance(out.columns, pd.MultiIndex):
            out.columns = [str(c[0]) for c in out.columns]
        out = out.loc[:, ~out.columns.duplicated()]
        wanted = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in out.columns]
        if "Close" not in wanted:
            return None
        out = out[wanted].dropna(subset=["Close"])
        return out if not out.empty else None
    except Exception:
        return None


def _fetch_one(symbol: str, exchange: str) -> Dict[str, Any]:
    """Fetch one symbol. Never raises."""
    yf_sym = yahoo_symbol(symbol, exchange)
    row: Dict[str, Any] = {
        "symbol": symbol, "exchange": exchange, "yahoo": yf_sym,
        "price": None, "change_pct": None, "volume": None,
        "asof": None, "source": None, "error": None,
    }
    try:
        import yfinance as yf
        frame = yf.download(
            yf_sym, period="10d", interval="1d",
            progress=False, auto_adjust=False, threads=False,
        )
        df = _flatten_ohlcv(frame)
        if df is None or len(df) < 2:
            row["error"] = "no data"
            return row
        close = df["Close"].astype(float)
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        row["price"] = round(last, 2)
        row["change_pct"] = round((last - prev) / prev * 100.0, 3) if prev else None
        if "Volume" in df.columns:
            try:
                row["volume"] = float(df["Volume"].iloc[-1])
            except Exception:
                row["volume"] = None
        row["asof"] = str(df.index[-1])[:10]
        row["source"] = "yfinance"
    except Exception as exc:
        row["error"] = str(exc)[:160]
    return row


def fetch_quotes(universe: Optional[Iterable[tuple]] = None) -> Dict[str, Dict[str, Any]]:
    """Fetch the full universe in parallel and cache the result for 60 s.

    Returns ``{"NSE:SBIN-EQ": {...}, "BSE:SBIN": {...}}``.
    """
    cached = _read_json(CACHE_PATH)
    if isinstance(cached, dict):
        try:
            age = (_dt.datetime.now() - _dt.datetime.fromisoformat(cached["fetched_at"])).total_seconds()
            if 0 <= age < CACHE_TTL_SECONDS and isinstance(cached.get("rows"), dict):
                return cached["rows"]
        except (KeyError, TypeError, ValueError):
            pass

    pairs = list(universe or ([(s, "NSE") for s in NSE_UNIVERSE] + [(s, "BSE") for s in BSE_UNIVERSE]))
    rows: Dict[str, Dict[str, Any]] = {}
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_fetch_one, s, ex): (s, ex) for s, ex in pairs}
            for future in futures:
                s, ex = futures[future]
                try:
                    rows[f"{ex}:{s}"] = future.result(timeout=25)
                except Exception as exc:
                    rows[f"{ex}:{s}"] = {
                        "symbol": s, "exchange": ex, "price": None,
                        "change_pct": None, "error": str(exc)[:160],
                    }
    except Exception:
        pass

    if rows:
        _write_json(CACHE_PATH, {
            "fetched_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "rows": rows,
        })
    return rows


def load_signals() -> Dict[str, Any]:
    """Load the institutional / insider / corporate event store."""
    data = _read_json(SIGNALS_PATH)
    return data if isinstance(data, dict) else {}


def _signal_score(payload: Dict[str, Any], section: str) -> Dict[str, Dict[str, float]]:
    """Normalize a signals section into ``{SYMBOL: {"score": x, "n": k}}``."""
    out: Dict[str, Dict[str, float]] = {}
    block = payload.get(section) if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return out
    for sym, items in block.items():
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            continue
        values: List[float] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                values.append(max(-1.0, min(1.0, float(item.get("score", 0.0)))))
            except (TypeError, ValueError):
                continue
        if values:
            out[str(sym).upper()] = {
                "score": round(sum(values) / len(values), 3),
                "n": float(len(values)),
            }
    return out


def _base_symbol(symbol: str) -> str:
    sym = str(symbol).upper()
    for suffix in ("-EQ", "-BE", "-BZ"):
        if sym.endswith(suffix):
            sym = sym[:-3]
    return sym


def build_unified_view(allow_network: bool = True) -> Dict[str, Any]:
    """Merge quotes + event signals into a single unified view.

    The composite score (``-1..1``) blends, when present:

    * price momentum (today's change, saturating at ±5%),
    * institutional flow score,
    * insider filing score,
    * corporate event score.

    Weights are renormalized over the sources that actually exist, so a symbol
    with only a quote is not penalised for missing flow data — its coverage is
    simply reported as low.
    """
    quotes = fetch_quotes() if allow_network else {}
    signals = load_signals()
    flow = _signal_score(signals, "institutional_flow")
    insider = _signal_score(signals, "insider_filing")
    corporate = _signal_score(signals, "corporate_release")
    govt = _signal_score(signals, "govt_release")

    merged: List[Dict[str, Any]] = []
    by_base: Dict[str, Dict[str, Any]] = {}

    for key, row in (quotes or {}).items():
        base = _base_symbol(row.get("symbol") or key)
        change = row.get("change_pct")
        momentum = None
        if isinstance(change, (int, float)):
            momentum = max(-1.0, min(1.0, float(change) / 5.0))

        parts: List[tuple] = []
        if momentum is not None:
            parts.append(("momentum", momentum, 0.40))
        if base in flow:
            parts.append(("institutional_flow", flow[base]["score"], 0.30))
        if base in insider:
            parts.append(("insider", insider[base]["score"], 0.15))
        if base in corporate:
            parts.append(("corporate", corporate[base]["score"], 0.15))

        weight_sum = sum(w for _, _, w in parts)
        composite = (
            round(sum(v * w for _, v, w in parts) / weight_sum, 4) if weight_sum else None
        )
        coverage = len(parts) / 4.0

        entry = {
            "symbol": row.get("symbol"),
            "exchange": row.get("exchange"),
            "base_symbol": base,
            "price": row.get("price"),
            "change_pct": change,
            "volume": row.get("volume"),
            "asof": row.get("asof"),
            "source": row.get("source"),
            "error": row.get("error"),
            "composite_score": composite,
            "coverage": round(coverage, 2),
            "components": {name: round(v, 3) for name, v, _ in parts},
        }
        merged.append(entry)
        # Keep the first exchange row as primary, but prefer a populated one.
        if base not in by_base or (by_base[base].get("price") is None and entry.get("price") is not None):
            by_base[base] = entry

    changes = [r["change_pct"] for r in merged if isinstance(r.get("change_pct"), (int, float))]
    advances = sum(1 for c in changes if c > 0)
    declines = sum(1 for c in changes if c < 0)
    breadth = None
    if changes:
        breadth = round((advances - declines) / float(len(changes)), 3)

    index_levels: Dict[str, Any] = {}
    if allow_network:
        index_levels = _fetch_index_levels()

    return {
        "schema": SCHEMA_VERSION,
        "asof": _dt.datetime.now().isoformat(timespec="seconds"),
        "universe": {"nse": NSE_UNIVERSE, "bse": BSE_UNIVERSE},
        "sources": SOURCE_REGISTRY,
        "rows": merged,
        "primary": list(by_base.values()),
        "breadth": {
            "advances": advances,
            "declines": declines,
            "unchanged": len(changes) - advances - declines,
            "score": breadth,
            "coverage": len(changes),
        },
        "index_levels": index_levels,
        "signals_present": {
            "institutional_flow": sorted(flow),
            "insider_filing": sorted(insider),
            "corporate_release": sorted(corporate),
            "govt_release": sorted(govt),
        },
        "coverage": round(
            (len(changes) + len(flow) + len(insider) + len(corporate))
            / float(max(1, len(merged) + 3 * len(by_base))),
            3,
        ),
    }


def _fetch_index_levels() -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    try:
        import yfinance as yf
        frame = yf.download(
            list(_INDEX_PROXIES.values()), period="5d", interval="1d",
            progress=False, auto_adjust=False, group_by="ticker", threads=False,
        )
        for name, sym in _INDEX_PROXIES.items():
            try:
                sub = frame[sym] if hasattr(frame.columns, "levels") else frame
                close = sub["Close"].dropna()
                if len(close) >= 2:
                    last, prev = float(close.iloc[-1]), float(close.iloc[-2])
                    out[name] = {
                        "level": round(last, 2),
                        "change_pct": round((last - prev) / prev * 100.0, 3) if prev else None,
                        "source": "yfinance",
                    }
            except Exception:
                continue
    except Exception:
        return out
    return out
