"""
data.pit_store — point-in-time OHLCV store (Parquet + manifest).
==============================================================

Every backtest in ZERO read ``yfinance`` live. That has three consequences
nobody can undo afterwards: the series is a *vendored, adjusted snapshot* (so a
split silently rewrites history), it cannot be reproduced if the vendor changes,
and there is no record of when a bar became knowable. A point-in-time store
fixes all three:

* one Parquet file per symbol, appended only — patches are new rows keyed by
  ``(symbol, date, revision)``;
* a manifest per symbol recording vendor, row count, first/last bar, the
  ``known_at`` timestamp of the newest revision and a content fingerprint;
* ``as_of(symbol, date)`` returns only bars whose revision was knowable by that
  date, which is what a genuinely honest backtest needs.

Layout::

    db/pit/<SYMBOL>/bars.parquet
    db/pit/<SYMBOL>/manifest.json

Environment: ``ZERO_PIT_DIR`` (default ``db/pit``).

Degrades cleanly: if ``pyarrow`` is unavailable, writes raise a clear
``RuntimeError`` instead of silently falling back to a CSV that would break the
provenance guarantee. Reads work from any engine Parquet supports.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

DEFAULT_DIR = os.getenv("ZERO_PIT_DIR", os.path.join("db", "pit"))
REQUIRED_COLUMNS = ("open", "high", "low", "close")


def _dir(root: Optional[str] = None) -> str:
    return root or DEFAULT_DIR


def _symbol_dir(symbol: str, root: Optional[str] = None) -> str:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in symbol.upper())
    return os.path.join(_dir(root), safe)


def bars_path(symbol: str, root: Optional[str] = None) -> str:
    return os.path.join(_symbol_dir(symbol, root), "bars.parquet")


def manifest_path(symbol: str, root: Optional[str] = None) -> str:
    return os.path.join(_symbol_dir(symbol, root), "manifest.json")


def _require_pandas():
    try:
        import pandas as pd
        return pd
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("pandas is required for the point-in-time store") from exc


def _require_parquet() -> None:
    try:
        import pyarrow  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "pyarrow is required to write the point-in-time store "
            "(pip install pyarrow). Refusing to fall back to CSV."
        ) from exc


def _normalise(df):
    """Lower-case columns, DatetimeIndex named ``date``, validated OHLC."""
    pd = _require_pandas()
    out = df.copy()
    out.columns = [str(c).strip().lower() for c in out.columns]
    if "timestamps" in out.columns and not isinstance(out.index, pd.DatetimeIndex):
        out = out.rename(columns={"timestamps": "date"}).set_index("date")
    if "date" in out.columns and not isinstance(out.index, pd.DatetimeIndex):
        out = out.set_index("date")
    if not isinstance(out.index, pd.DatetimeIndex):
        raise ValueError("point-in-time frames need a DatetimeIndex (or a date/timestamps column)")
    out.index = pd.DatetimeIndex(out.index).normalize()
    out.index.name = "date"
    missing = [c for c in REQUIRED_COLUMNS if c not in out.columns]
    if missing:
        raise ValueError(f"missing OHLC columns: {missing}")
    for c in REQUIRED_COLUMNS:
        out[c] = out[c].astype(float)
    if "volume" not in out.columns:
        out["volume"] = 0.0
    out["volume"] = out["volume"].astype(float)
    bad = (
        (out["high"] < out[["open", "close"]].max(axis=1))
        | (out["low"] > out[["open", "close"]].min(axis=1))
        | (out["low"] <= 0)
    )
    if int(bad.sum()):
        raise ValueError(f"{int(bad.sum())} bars violate OHLC ordering; refusing to store")
    return out.sort_index()


def read(symbol: str, root: Optional[str] = None):
    """All stored bars for ``symbol`` (empty DataFrame when nothing is stored)."""
    pd = _require_pandas()
    path = bars_path(symbol, root)
    if not os.path.exists(path):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.read_parquet(path)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
    return df.sort_index()


def write(symbol: str, df, vendor: str, known_at: Optional[float] = None,
          revision: Optional[int] = None, root: Optional[str] = None) -> Dict[str, Any]:
    """
    Append (or revise) bars for ``symbol``.

    Existing ``(date, revision)`` pairs are replaced, never duplicated, so the
    same snapshot can be re-ingested idempotently. ``known_at`` defaults to now
    and is the field ``as_of`` filters on.
    """
    pd = _require_pandas()
    _require_parquet()
    from engine import data_provenance as prov_mod

    frame = _normalise(df)
    if not len(frame):
        return {"status": "EMPTY", "symbol": symbol, "rows": 0}

    known_at = float(known_at if known_at is not None else time.time())
    revision = int(revision if revision is not None else int(known_at))
    frame = frame.reset_index()
    frame["source"] = vendor
    frame["known_at"] = known_at
    frame["revision"] = revision

    path = bars_path(symbol, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = read(symbol, root)
    if len(existing):
        existing = existing.reset_index()
        for col in ("source", "known_at", "revision"):
            if col not in existing.columns:
                existing[col] = vendor if col == "source" else 0
        combined = pd.concat([existing, frame], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "revision"], keep="last")
    else:
        combined = frame
    combined = combined.sort_values(["date", "revision"]).reset_index(drop=True)
    combined.to_parquet(path, index=False)

    full = read(symbol, root)
    prov = prov_mod.classify_frame(full.reset_index(), source=prov_mod.REAL,
                                   vendor=vendor, symbol=symbol)
    manifest = {
        "symbol": symbol,
        "vendor": vendor,
        "rows": int(len(full)),
        "revisions": int(full["revision"].nunique()) if "revision" in full else 1,
        "first_bar": str(full.index.min())[:10] if len(full) else None,
        "last_bar": str(full.index.max())[:10] if len(full) else None,
        "last_known_at": known_at,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "provenance": prov.as_dict(),
    }
    with open(manifest_path(symbol, root), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    return {"status": "OK", "symbol": symbol, "rows": int(len(full)),
            "appended": int(len(frame)), "manifest": manifest}


def as_of(symbol: str, date: str, root: Optional[str] = None):
    """
    Bars knowable on or before ``date``.

    With multiple revisions of the same bar, the latest revision that was
    already knowable wins — this is the whole point of the store: no
    future-patched value can leak into a historical decision.
    """
    pd = _require_pandas()
    df = read(symbol, root)
    if not len(df):
        return df
    cutoff = pd.Timestamp(date)
    if "known_at" in df.columns:
        df = df[df["known_at"] <= cutoff.timestamp() + 86_399]
    df = df[df.index <= cutoff]
    if "revision" in df.columns and len(df):
        df = (df.sort_values(["date", "revision"])
                .groupby(level=0, as_index=False)
                .last())
        if "date" in df.columns:
            df = df.set_index("date")
    drop = [c for c in ("known_at", "revision", "source") if c in df.columns]
    return df.drop(columns=drop) if drop else df


def verify(symbol: Optional[str] = None, root: Optional[str] = None) -> Dict[str, Any]:
    """Gap/duplicate/anomaly check across one symbol or the whole store."""
    pd = _require_pandas()
    base = _dir(root)
    symbols: List[str] = []
    if symbol:
        symbols = [symbol]
    elif os.path.isdir(base):
        symbols = [d for d in sorted(os.listdir(base))
                   if os.path.isdir(os.path.join(base, d))]
    report: Dict[str, Any] = {"symbols": len(symbols), "issues": [], "clean": True}
    for sym in symbols:
        try:
            df = read(sym, root)
            if not len(df):
                report["issues"].append({"symbol": sym, "issue": "no bars"})
                continue
            dupes = int(df.index.duplicated().sum())
            gaps = int((df.index.to_series().diff().dt.days > 5).sum())
            anomalies = int(((df["high"] < df["close"]) | (df["low"] > df["close"])).sum())
            stale_days = (pd.Timestamp.today().normalize() - df.index.max()).days
            if dupes or anomalies or stale_days > 10:
                report["issues"].append({
                    "symbol": sym, "duplicate_dates": dupes,
                    "ohlc_anomalies": anomalies, "stale_days": int(stale_days),
                    "weekend_gaps": gaps,
                })
                report["clean"] = False
        except Exception as exc:  # noqa: BLE001
            report["issues"].append({"symbol": sym, "issue": str(exc)})
            report["clean"] = False
    return report


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2, default=str))
