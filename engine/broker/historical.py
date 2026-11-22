"""
engine.broker.historical — broker-grade daily history, one interface.
=====================================================================

``data.historical`` + yfinance is a single vendor with silent adjustment
surprises. This module adds the missing second and third sources so bar
integrity can be *checked* rather than assumed:

* **Zerodha Kite** — ``kiteconnect`` SDK, needs ``ZERO_KITE_API_KEY`` and
  ``ZERO_KITE_ACCESS_TOKEN``;
* **Upstox** — v2 REST, needs ``ZERO_UPSTOX_ACCESS_TOKEN``;
* **Dhan** — v2 REST, needs ``ZERO_DHAN_ACCESS_TOKEN`` and
  ``ZERO_DHAN_CLIENT_ID``.

Every adapter returns the same normalised frame (lower-case OHLCV with a
``timestamps`` column) tagged with provenance, so ``data.pit_store`` can store
it and the AITE exam can gate on it. ``available()`` is honest: without
credentials an adapter reports itself unavailable instead of returning a
fabricated series.

Nothing here imports an SDK or touches the network at import time.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

TIMEOUT = float(os.getenv("ZERO_HIST_TIMEOUT", "15"))

# Index symbol -> per-adapter instrument identifiers (override via env).
KITE_SYMBOLS = {"NIFTY 50": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK",
                "SENSEX": "BSE:SENSEX"}
UPSTOX_SYMBOLS = {"NIFTY 50": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank",
                  "SENSEX": "BSE_INDEX|SENSEX"}
DHAN_SYMBOLS = {"NIFTY 50": "13", "BANKNIFTY": "25", "SENSEX": "51"}


def _env(*names: str) -> str:
    for n in names:
        v = (os.getenv(n) or "").strip()
        if v:
            return v
    return ""


def _normalise(rows: List[Dict[str, Any]], symbol: str, vendor: str):
    import pandas as pd
    from engine import data_provenance as prov_mod

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["timestamps"] = pd.to_datetime(df["timestamps"])
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    return prov_mod.tag_frame(df, source=prov_mod.REAL, vendor=vendor, symbol=symbol)


@dataclass
class Adapter:
    name: str
    vendor: str

    def available(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def fetch(self, symbol: str, days: int = 400):  # pragma: no cover - interface
        raise NotImplementedError

    def missing(self) -> List[str]:
        return []


class KiteHistorical(Adapter):
    def __init__(self) -> None:
        super().__init__(name="kite", vendor="zerodha_kite")

    def missing(self) -> List[str]:
        return [v for v in ("ZERO_KITE_API_KEY", "ZERO_KITE_ACCESS_TOKEN") if not _env(v)]

    def available(self) -> bool:
        if self.missing():
            return False
        try:
            import kiteconnect  # noqa: F401
            return True
        except Exception:
            return False

    def fetch(self, symbol: str, days: int = 400):
        import pandas as pd
        from datetime import date, timedelta

        if not self.available():
            raise RuntimeError(
                "Kite adapter unavailable: missing " + ", ".join(self.missing() or ["kiteconnect"])
            )
        from kiteconnect import KiteConnect

        kite = KiteConnect(api_key=_env("ZERO_KITE_API_KEY"))
        kite.set_access_token(_env("ZERO_KITE_ACCESS_TOKEN"))
        token = KITE_SYMBOLS.get(symbol, symbol)
        to_d = date.today()
        from_d = to_d - timedelta(days=max(30, int(days * 1.6)))
        raw = kite.historical_data(token, from_d, to_d, "day")
        rows = [{
            "timestamps": r.get("date"),
            "open": r.get("open"), "high": r.get("high"),
            "low": r.get("low"), "close": r.get("close"),
            "volume": r.get("volume", 0),
        } for r in raw]
        return _normalise(rows, symbol, self.vendor)


class UpstoxHistorical(Adapter):
    BASE = "https://api.upstox.com/v2/historical-candle"

    def __init__(self) -> None:
        super().__init__(name="upstox", vendor="upstox_v2")

    def missing(self) -> List[str]:
        return [] if _env("ZERO_UPSTOX_ACCESS_TOKEN") else ["ZERO_UPSTOX_ACCESS_TOKEN"]

    def available(self) -> bool:
        return not self.missing()

    def fetch(self, symbol: str, days: int = 400):
        if not self.available():
            raise RuntimeError("Upstox adapter unavailable: missing " + ", ".join(self.missing()))
        from datetime import date, timedelta

        to_d = date.today()
        from_d = to_d - timedelta(days=max(30, int(days * 1.6)))
        key = UPSTOX_SYMBOLS.get(symbol, symbol)
        url = (f"{self.BASE}/{urllib.parse.quote(key)}/day/"
               f"{from_d.isoformat()}/{to_d.isoformat()}")
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {_env('ZERO_UPSTOX_ACCESS_TOKEN')}",
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
        candles = ((payload.get("data") or {}).get("candles") or [])
        rows = [{
            "timestamps": c[0], "open": c[1], "high": c[2], "low": c[3],
            "close": c[4], "volume": c[5] if len(c) > 5 else 0,
        } for c in candles if len(c) >= 5]
        return _normalise(rows, symbol, self.vendor)


class DhanHistorical(Adapter):
    BASE = "https://api.dhan.co/v2/charts/historical"

    def __init__(self) -> None:
        super().__init__(name="dhan", vendor="dhan_v2")

    def missing(self) -> List[str]:
        return [v for v in ("ZERO_DHAN_ACCESS_TOKEN", "ZERO_DHAN_CLIENT_ID") if not _env(v)]

    def available(self) -> bool:
        return not self.missing()

    def fetch(self, symbol: str, days: int = 400):
        if not self.available():
            raise RuntimeError("Dhan adapter unavailable: missing " + ", ".join(self.missing()))
        from datetime import date, timedelta

        to_d = date.today()
        from_d = to_d - timedelta(days=max(30, int(days * 1.6)))
        body = json.dumps({
            "securityId": DHAN_SYMBOLS.get(symbol, symbol),
            "exchangeSegment": "IDX_I",
            "instrument": "INDEX",
            "fromDate": from_d.isoformat(),
            "toDate": to_d.isoformat(),
        }).encode()
        req = urllib.request.Request(self.BASE, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": _env("ZERO_DHAN_ACCESS_TOKEN"),
            "client-id": _env("ZERO_DHAN_CLIENT_ID"),
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode())
        stamps = payload.get("timestamp") or []
        rows = []
        for i, ts in enumerate(stamps):
            try:
                rows.append({
                    "timestamps": time.strftime("%Y-%m-%d", time.gmtime(int(ts))),
                    "open": payload["open"][i], "high": payload["high"][i],
                    "low": payload["low"][i], "close": payload["close"][i],
                    "volume": (payload.get("volume") or [0] * len(stamps))[i],
                })
            except (KeyError, IndexError, TypeError, ValueError):
                continue
        return _normalise(rows, symbol, self.vendor)


ADAPTERS: Dict[str, Adapter] = {
    "kite": KiteHistorical(),
    "upstox": UpstoxHistorical(),
    "dhan": DhanHistorical(),
}


def status() -> Dict[str, Any]:
    """Which sources can serve history right now, and what each is missing."""
    return {
        name: {"available": a.available(), "missing_env": a.missing()}
        for name, a in ADAPTERS.items()
    }


def available_adapters() -> List[str]:
    return [n for n, a in ADAPTERS.items() if a.available()]


def fetch_history(symbol: str, days: int = 400, prefer: Optional[str] = None,
                  store: bool = True) -> Dict[str, Any]:
    """
    Fetch history from the first working broker adapter.

    When ``store`` is true the frame is written to the point-in-time store, so
    the backtest reads an unchangeable, provenance-tagged copy. Returns a dict
    with ``status``, ``vendor``, ``rows`` and ``frame`` (None on failure).
    """
    order = ([prefer] if prefer else []) + [n for n in ("kite", "upstox", "dhan") if n != prefer]
    errors: List[str] = []
    for name in order:
        adapter = ADAPTERS.get(name)
        if adapter is None or not adapter.available():
            continue
        try:
            frame = adapter.fetch(symbol, days=days)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            continue
        if frame is None or not len(frame):
            errors.append(f"{name}: empty response")
            continue
        result: Dict[str, Any] = {
            "status": "OK", "vendor": adapter.vendor, "symbol": symbol,
            "rows": int(len(frame)), "frame": frame, "errors": errors,
        }
        if store:
            try:
                from data import pit_store
                result["stored"] = pit_store.write(symbol, frame, vendor=adapter.vendor)
            except Exception as exc:  # noqa: BLE001
                result["stored"] = {"status": "ERROR", "error": str(exc)}
        return result
    return {"status": "UNAVAILABLE", "symbol": symbol, "rows": 0,
            "frame": None, "errors": errors or ["no broker credentials configured"],
            "status_detail": status()}


def cross_check(symbol: str, days: int = 400) -> Dict[str, Any]:
    """
    Compare every available broker against the stored/vendor series.

    Reports per-date close differences (max and median bps). A large divergence
    between two independent sources is the cheapest possible corporate-action /
    adjustment detector.
    """
    import pandas as pd

    frames: Dict[str, Any] = {}
    for name in available_adapters():
        try:
            f = ADAPTERS[name].fetch(symbol, days=days)
            if f is not None and len(f):
                frames[name] = f.set_index(pd.to_datetime(f["timestamps"]))["close"].astype(float)
        except Exception:
            continue
    if len(frames) < 2:
        return {"status": "INSUFFICIENT_SOURCES", "available": list(frames),
                "note": "need at least two broker credentials to cross-check"}
    base_name = list(frames)[0]
    report: Dict[str, Any] = {"status": "OK", "base": base_name, "comparisons": []}
    for other in list(frames)[1:]:
        joined = pd.concat([frames[base_name], frames[other]], axis=1, join="inner").dropna()
        if not len(joined):
            report["comparisons"].append({"vs": other, "overlap_days": 0})
            continue
        diff_bps = ((joined.iloc[:, 1] - joined.iloc[:, 0]).abs() / joined.iloc[:, 0] * 10_000)
        report["comparisons"].append({
            "vs": other,
            "overlap_days": int(len(joined)),
            "max_diff_bps": round(float(diff_bps.max()), 2),
            "median_diff_bps": round(float(diff_bps.median()), 2),
            "suspicious": bool(float(diff_bps.max()) > 100),
        })
    return report


if __name__ == "__main__":
    print(json.dumps(status(), indent=2))
