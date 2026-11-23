"""
Trading-panel data: F&O board, commodities tape, sector heatmap.

Every function returns JSON-able dicts and a ``source``/``delay`` disclosure, so
the panel never presents a delayed print as a tick. Free sources only:

* **NSE option chain** — ``data.options_chain.fetch_nse_option_chain`` (1–3 s
  delayed, often rate-limited; the existing cache + last-good layer handles it).
* **yfinance** — index spot, global futures, Indian large caps for the heatmap.

No broker token, no paid API, no scraping of a login-walled site.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import numpy as np

from engine.v21 import indices as idx

_TTL = 45.0
_cache: Dict[str, Dict[str, Any]] = {}

# Heavyweight map for the "FINVIZ-style" Indian heatmap. Weights are approximate
# free-float weights; they only drive tile size, never a P&L claim.
HEATMAP_UNIVERSE: List[Dict[str, Any]] = [
    {"symbol": "RELIANCE.NS", "name": "Reliance", "sector": "Energy", "w": 10.2},
    {"symbol": "TCS.NS", "name": "TCS", "sector": "IT", "w": 4.3},
    {"symbol": "HDFCBANK.NS", "name": "HDFC Bank", "sector": "Financials", "w": 8.4},
    {"symbol": "ICICIBANK.NS", "name": "ICICI Bank", "sector": "Financials", "w": 6.1},
    {"symbol": "INFY.NS", "name": "Infosys", "sector": "IT", "w": 4.0},
    {"symbol": "BHARTIARTL.NS", "name": "Bharti Airtel", "sector": "Telecom", "w": 4.2},
    {"symbol": "SBIN.NS", "name": "SBI", "sector": "Financials", "w": 3.2},
    {"symbol": "LT.NS", "name": "L&T", "sector": "Industrials", "w": 3.0},
    {"symbol": "ITC.NS", "name": "ITC", "sector": "FMCG", "w": 3.4},
    {"symbol": "HINDUNILVR.NS", "name": "HUL", "sector": "FMCG", "w": 2.6},
    {"symbol": "AXISBANK.NS", "name": "Axis Bank", "sector": "Financials", "w": 2.4},
    {"symbol": "KOTAKBANK.NS", "name": "Kotak Bank", "sector": "Financials", "w": 2.3},
    {"symbol": "MARUTI.NS", "name": "Maruti", "sector": "Auto", "w": 2.2},
    {"symbol": "SUNPHARMA.NS", "name": "Sun Pharma", "sector": "Pharma", "w": 2.1},
    {"symbol": "TATAMOTORS.NS", "name": "Tata Motors", "sector": "Auto", "w": 2.0},
    {"symbol": "BAJFINANCE.NS", "name": "Bajaj Finance", "sector": "Financials", "w": 2.5},
    {"symbol": "HCLTECH.NS", "name": "HCL Tech", "sector": "IT", "w": 1.9},
    {"symbol": "ADANIENT.NS", "name": "Adani Ent", "sector": "Industrials", "w": 1.8},
    {"symbol": "TITAN.NS", "name": "Titan", "sector": "Consumer", "w": 1.7},
    {"symbol": "ASIANPAINT.NS", "name": "Asian Paints", "sector": "Consumer", "w": 1.5},
    {"symbol": "NTPC.NS", "name": "NTPC", "sector": "Utilities", "w": 1.9},
    {"symbol": "POWERGRID.NS", "name": "Power Grid", "sector": "Utilities", "w": 1.5},
    {"symbol": "WIPRO.NS", "name": "Wipro", "sector": "IT", "w": 1.2},
    {"symbol": "ONGC.NS", "name": "ONGC", "sector": "Energy", "w": 1.2},
]

COMMODITIES = [
    {"key": "gold", "name": "Gold", "ticker": "GC=F", "unit": "USD/oz"},
    {"key": "silver", "name": "Silver", "ticker": "SI=F", "unit": "USD/oz"},
    {"key": "crude", "name": "WTI Crude", "ticker": "CL=F", "unit": "USD/bbl"},
    {"key": "brent", "name": "Brent", "ticker": "BZ=F", "unit": "USD/bbl"},
    {"key": "copper", "name": "Copper", "ticker": "HG=F", "unit": "USD/lb"},
    {"key": "natgas", "name": "Natural Gas", "ticker": "NG=F", "unit": "USD/MMBtu"},
    {"key": "corn", "name": "Corn", "ticker": "ZC=F", "unit": "USc/bu"},
    {"key": "aluminium", "name": "Aluminium", "ticker": "ALI=F", "unit": "USD/lb"},
]

# How physical cargo actually moves. Reference knowledge (not live data): each
# stage names the public source the panel links to.
SUPPLY_CHAIN = [
    {"stage": "Mine", "what": "extraction, grade and concentrate",
     "watch": "mine output, strike action, grade decline",
     "source": "USGS Mineral Commodity Summaries",
     "url": "https://pubs.usgs.gov/periodicals/mcs2024/mcs2024.pdf"},
    {"stage": "Ship", "what": "freight, Baltic indices, canal chokepoints",
     "watch": "Baltic Dry/Capesize rates, Red Sea and Panama transits",
     "source": "Baltic Exchange",
     "url": "https://www.balticexchange.com/en/index.html"},
    {"stage": "Port", "what": "berth queues, demurrage, customs",
     "watch": "port congestion and vessel waiting days",
     "source": "UNCTAD Review of Maritime Transport",
     "url": "https://unctad.org/publication/review-maritime-transport"},
    {"stage": "Mill", "what": "smelting, refining, offtake contracts",
     "watch": "treatment charges (TC/RC), mill utilisation",
     "source": "LME / Fastmarkets published TC-RC benchmarks",
     "url": "https://www.lme.com/"},
]


def _cached(key: str, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit["_ts"]) < _TTL:
        return {**hit["value"], "cache_age_s": round(now - hit["_ts"], 1)}
    value = fn()
    _cache[key] = {"_ts": now, "value": value}
    return {**value, "cache_age_s": 0.0}


def _yf_quotes(tickers: List[str], period: str = "5d") -> Dict[str, Dict[str, Any]]:
    """Batch yfinance fetch → {ticker: {last, prev, change_pct}}."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        import yfinance as yf
        data = yf.download(tickers, period=period, interval="1d",
                           progress=False, auto_adjust=False, group_by="ticker", threads=True)
    except Exception as exc:  # noqa: BLE001
        return {"__error__": {"reason": f"{type(exc).__name__}: {exc}"}}
    for t in tickers:
        try:
            sub = data[t] if len(tickers) > 1 else data
            closes = sub["Close"].dropna()
            if len(closes) < 2:
                continue
            last = float(closes.iloc[-1])
            prev = float(closes.iloc[-2])
            out[t] = {"last": round(last, 4), "prev": round(prev, 4),
                      "change_pct": round((last / prev - 1.0) * 100.0, 2) if prev else None}
        except Exception:
            continue
    return out


def fno_board(index: str = "NIFTY") -> Dict[str, Any]:
    """Spot + option-chain summary (+ GEX if the chain is reachable)."""
    key = idx.canonical(index)

    def _build() -> Dict[str, Any]:
        q = idx.quote(key, force=True)
        chain = None
        try:
            from data.options_chain import fetch_and_process
            chain = fetch_and_process(idx.UNIVERSE[key]["nse"])
        except Exception as exc:  # noqa: BLE001
            chain = {"error": f"{type(exc).__name__}: {exc}"}
        gex = None
        try:
            from engine.v21 import gex as gex_mod
            gex = gex_mod.live(key)
        except Exception as exc:  # noqa: BLE001
            gex = {"available": False, "reason": str(exc)}
        return {
            "index": key,
            "label": idx.label(key),
            "quote": q,
            "chain": chain or {"error": "no chain"},
            "gex": gex,
            "source": "NSE (chain) · yfinance/live_index_service (spot)",
            "delay": "option chain 1–3 s delayed; spot 15 s cache",
            "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    return _cached(f"fno:{key}", _build)


def _depth(leg: Dict[str, Any], side: str) -> Optional[float]:
    """Displayed quantity on one side of a leg (total pending, else top of book)."""
    keys = ("totalBuyQuantity", "bidQty") if side == "bid" else ("totalSellQuantity", "askQty")
    for k in keys:
        v = leg.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None


def option_table(index: str = "NIFTY", max_rows: int = 40) -> List[Dict[str, Any]]:
    """Flat CE/PE rows around the money, for the trading panel's table."""
    key = idx.canonical(index)
    try:
        from data.options_chain import fetch_nse_option_chain
        payload = fetch_nse_option_chain(idx.UNIVERSE[key]["nse"])
    except Exception:
        return []
    if not payload:
        return []
    records = payload.get("records", {}) or {}
    rows = payload.get("filtered", {}).get("data") or records.get("data") or []
    spot = float(records.get("underlyingValue") or 0)
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: abs(float(r.get("strikePrice", 0)) - spot))[:max_rows]
    out = []
    for r in sorted(rows, key=lambda r: float(r.get("strikePrice", 0))):
        ce, pe = r.get("CE") or {}, r.get("PE") or {}
        out.append({
            "strike": r.get("strikePrice"),
            "call_oi": ce.get("openInterest"),
            "call_oi_chg": ce.get("changeinOpenInterest"),
            "call_iv": ce.get("impliedVolatility"),
            "call_ltp": ce.get("lastPrice"),
            "put_oi": pe.get("openInterest"),
            "put_oi_chg": pe.get("changeinOpenInterest"),
            "put_iv": pe.get("impliedVolatility"),
            "put_ltp": pe.get("lastPrice"),
            "call_bid_qty": _depth(ce, "bid"),
            "call_ask_qty": _depth(ce, "ask"),
            "put_bid_qty": _depth(pe, "bid"),
            "put_ask_qty": _depth(pe, "ask"),
        })
    return out


def commodities() -> Dict[str, Any]:
    """Live futures tape + the physical supply-chain knowledge table."""

    def _build() -> Dict[str, Any]:
        quotes = _yf_quotes([c["ticker"] for c in COMMODITIES])
        rows, missing = [], []
        for c in COMMODITIES:
            q = quotes.get(c["ticker"])
            if not q:
                missing.append(c["name"])
                continue
            rows.append({**c, **q})
        return {
            "available": bool(rows),
            "rows": rows,
            "missing": missing,
            "supply_chain": SUPPLY_CHAIN,
            "source": "yfinance front-month futures",
            "delay": "futures quotes are delayed 10–20 min on the free feed",
            "error": quotes.get("__error__"),
        }

    return _cached("commodities", _build)


def heatmap(index: str = "NIFTY") -> Dict[str, Any]:
    """Sector/constituent heatmap computed from live quotes."""

    def _build() -> Dict[str, Any]:
        quotes = _yf_quotes([r["symbol"] for r in HEATMAP_UNIVERSE])
        rows, missing = [], []
        for r in HEATMAP_UNIVERSE:
            q = quotes.get(r["symbol"])
            if not q:
                missing.append(r["name"])
                continue
            rows.append({**r, "last": q["last"], "change_pct": q["change_pct"],
                         "color": _heat_color(q["change_pct"])})
        sectors: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            s = sectors.setdefault(r["sector"], {"sector": r["sector"], "weight": 0.0,
                                                 "wsum": 0.0, "names": []})
            s["weight"] += r["w"]
            s["wsum"] += r["w"] * (r["change_pct"] or 0.0)
            s["names"].append(r["name"])
        out_sectors = [{"sector": s["sector"],
                        "change_pct": round(s["wsum"] / s["weight"], 2) if s["weight"] else 0.0,
                        "weight": round(s["weight"], 2),
                        "names": s["names"]} for s in sectors.values()]
        out_sectors.sort(key=lambda x: x["change_pct"], reverse=True)
        breadth = (sum(1 for r in rows if (r["change_pct"] or 0) > 0), len(rows))
        return {
            "available": bool(rows),
            "index": idx.canonical(index),
            "rows": sorted(rows, key=lambda x: (x["change_pct"] or 0), reverse=True),
            "sectors": out_sectors,
            "breadth": {"advancing": breadth[0], "total": breadth[1]},
            "missing": missing,
            "source": "yfinance constituents",
            "error": quotes.get("__error__"),
        }

    return _cached(f"heat:{idx.canonical(index)}", _build)


def _heat_color(pct: Optional[float]) -> str:
    if pct is None:
        return "#333333"
    p = max(-3.0, min(3.0, float(pct)))
    if p >= 0:
        g = int(40 + (p / 3.0) * 175)
        return f"rgba(0,{g},110,0.85)"
    r = int(40 + (-p / 3.0) * 195)
    return f"rgba({r},{30},60,0.85)"


def index_returns(name: str, bars: int = 260) -> Optional["np.ndarray"]:
    """Daily returns for a v2.1 index, from the AITE loader (provenance-tagged)."""
    try:
        from engine.aite.exam import load_market_frame
        df = load_market_frame(idx.aite_symbol(name), bars=bars)
        if df is None or len(df) < 40:
            return None
        return np.diff(np.log(df["close"].astype(float).to_numpy()))[-bars:]
    except Exception:
        return None
