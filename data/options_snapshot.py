"""
data.options_snapshot — record the option chain, once per session.
=================================================================

The prediction engine clamps its expected move by max-CE / max-PE open
interest, but those numbers were fetched live and then forgotten: there was no
way to ask "what did the chain look like when that call was made?". Options
positioning decays fast and is the one input most worth keeping.

This module appends a compact snapshot per expiry to
``db/options/<SYMBOL>/<YYYY-MM-DD>.jsonl`` and derives the few aggregates the
rest of the app uses:

* **PCR** (put/call OI ratio),
* **max-OI strikes** (the walls the prediction matrix clamps to),
* **ATM IV and 25-delta-ish skew** when the vendor supplies IV,
* **total OI / change in OI** per side.

Fetching NSE requires a browser-like session (cookies + UA); failures are
reported, never papered over. ``ingest_payload()`` is the offline entry point so
a saved JSON payload can be scored in tests without touching the network.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

OPTIONS_DIR = os.getenv("ZERO_OPTIONS_DIR", os.path.join("db", "options"))
NSE_URL = "https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
NSE_HOME = "https://www.nseindia.com/option-chain"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
TIMEOUT = float(os.getenv("ZERO_OPTIONS_TIMEOUT", "12"))

SYMBOL_MAP = {"NIFTY 50": "NIFTY", "NIFTY": "NIFTY",
              "BANKNIFTY": "BANKNIFTY", "SENSEX": "SENSEX"}


def _path(symbol: str, day: Optional[str] = None) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in symbol.upper())
    day = day or time.strftime("%Y-%m-%d")
    return os.path.join(OPTIONS_DIR, safe, f"{day}.jsonl")


def fetch_chain(symbol: str = "NIFTY") -> Dict[str, Any]:
    """Best-effort NSE option-chain fetch. Returns ``{"status", "payload", ...}``."""
    key = SYMBOL_MAP.get(symbol.upper(), symbol.upper())
    opener = urllib.request.build_opener()
    opener.addheaders = [
        ("User-Agent", UA),
        ("Accept", "application/json, text/plain, */*"),
        ("Accept-Language", "en-US,en;q=0.9"),
        ("Referer", NSE_HOME),
    ]
    try:
        with opener.open(NSE_HOME, timeout=TIMEOUT) as home:
            cookies = home.headers.get_all("Set-Cookie") or []
        req = urllib.request.Request(NSE_URL.format(symbol=key))
        if cookies:
            req.add_header("Cookie", "; ".join(c.split(";")[0] for c in cookies))
        with opener.open(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return {"status": "OK", "payload": payload, "symbol": key}
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        return {"status": "ERROR", "error": str(exc), "symbol": key,
                "note": "NSE blocks non-browser clients; retry later or ingest a saved payload"}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_chain(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten an NSE payload into rows plus the aggregates the app consumes."""
    records = payload.get("records") or {}
    data = records.get("data") or []
    underlying = _num(records.get("underlyingValue"))
    expiry = str(records.get("expiryDates", [""])[0] if records.get("expiryDates") else "")

    rows: List[Dict[str, Any]] = []
    for item in data:
        strike = _num(item.get("strikePrice"))
        if strike <= 0:
            continue
        for side in ("CE", "PE"):
            leg = item.get(side) or {}
            if not leg:
                continue
            rows.append({
                "strike": strike,
                "side": side,
                "expiry": str(leg.get("expiryDate") or expiry),
                "last": _num(leg.get("lastPrice")),
                "oi": _num(leg.get("openInterest")),
                "oi_change": _num(leg.get("changeinOpenInterest")),
                "volume": _num(leg.get("totalTradedVolume")),
                "iv": _num(leg.get("impliedVolatility")),
            })
    if not rows:
        return {"status": "EMPTY", "rows": [], "underlying": underlying}

    ce = [r for r in rows if r["side"] == "CE"]
    pe = [r for r in rows if r["side"] == "PE"]
    ce_oi = sum(r["oi"] for r in ce)
    pe_oi = sum(r["oi"] for r in pe)

    def _wall(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not items:
            return None
        top = max(items, key=lambda r: r["oi"])
        return {"strike": top["strike"], "oi": top["oi"]}

    atm = min(rows, key=lambda r: abs(r["strike"] - underlying))["strike"] if underlying else None
    atm_ce = next((r for r in ce if r["strike"] == atm and r["iv"]), None)
    atm_pe = next((r for r in pe if r["strike"] == atm and r["iv"]), None)

    return {
        "status": "OK",
        "expiry": expiry,
        "underlying": underlying,
        "rows": rows,
        "pcr": round(pe_oi / ce_oi, 4) if ce_oi else None,
        "total_ce_oi": ce_oi,
        "total_pe_oi": pe_oi,
        "max_ce_oi_strike": _wall(ce),
        "max_pe_oi_strike": _wall(pe),
        "atm_strike": atm,
        "atm_iv_ce": atm_ce["iv"] if atm_ce else None,
        "atm_iv_pe": atm_pe["iv"] if atm_pe else None,
        "iv_skew": round((atm_pe["iv"] - atm_ce["iv"]), 3) if (atm_ce and atm_pe) else None,
    }


def ingest_payload(payload: Dict[str, Any], symbol: str = "NIFTY",
                   persist: bool = True) -> Dict[str, Any]:
    """Parse + append one snapshot. Returns the derived aggregates."""
    parsed = parse_chain(payload)
    parsed["symbol"] = symbol
    parsed["captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if parsed.get("status") != "OK":
        return parsed
    if persist:
        path = _path(symbol)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(parsed, default=str) + "\n")
            parsed["path"] = path
        except OSError as exc:
            parsed["persist_error"] = str(exc)
    return parsed


def snapshot(symbol: str = "NIFTY", persist: bool = True) -> Dict[str, Any]:
    """Fetch and store one snapshot (network)."""
    fetched = fetch_chain(symbol)
    if fetched.get("status") != "OK":
        return fetched
    return ingest_payload(fetched["payload"], symbol=symbol, persist=persist)


def latest(symbol: str = "NIFTY") -> Optional[Dict[str, Any]]:
    """Most recent stored snapshot for ``symbol``."""
    safe = "".join(c if c.isalnum() else "_" for c in symbol.upper())
    folder = os.path.join(OPTIONS_DIR, safe)
    if not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder), reverse=True):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(folder, name)
        try:
            last = None
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        last = line
            if last:
                return json.loads(last)
        except (OSError, json.JSONDecodeError):
            continue
    return None


def oi_history(symbol: str = "NIFTY", limit: int = 60) -> List[Dict[str, Any]]:
    """Chronological PCR / wall history (one point per stored snapshot)."""
    safe = "".join(c if c.isalnum() else "_" for c in symbol.upper())
    folder = os.path.join(OPTIONS_DIR, safe)
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(folder, name), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    out.append({
                        "captured_at": row.get("captured_at"),
                        "pcr": row.get("pcr"),
                        "underlying": row.get("underlying"),
                        "max_ce_oi_strike": (row.get("max_ce_oi_strike") or {}).get("strike"),
                        "max_pe_oi_strike": (row.get("max_pe_oi_strike") or {}).get("strike"),
                    })
        except OSError:
            continue
    return out[-limit:]


if __name__ == "__main__":
    print(json.dumps(snapshot("NIFTY"), indent=2, default=str)[:3000])
