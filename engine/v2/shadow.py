"""
engine.v2.shadow — paper-forward shadow runner + reconciliation.
===============================================================

A backtest only says the edge existed in the past. The missing step between
"14 of 20 specs pass" and "put money behind it" is a **forward record**: run the
exact frozen rules on new bars, write the signal before the outcome is known,
then score it against what actually happened.

This module is that loop, and it is deliberately dumb:

* ``record_day()`` evaluates every validated spec on the latest bar and appends
  one JSONL row per live signal (with a content hash so a signal cannot be
  silently edited later);
* ``score_day()`` scores signals whose horizon has now elapsed and appends the
  realised forward return;
* ``reconciliation()`` compares live hit-rate / expectancy with the backtest's
  own numbers and flags decay.

It never places an order and never needs a broker. Run it from the scheduler
after the close: ``python -m engine.v2.shadow --record --score``.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

SHADOW_DIR = os.path.join("db", "v2")
SIGNALS_PATH = os.path.join(SHADOW_DIR, "shadow_signals.jsonl")
SCORES_PATH = os.path.join(SHADOW_DIR, "shadow_scores.jsonl")

DEFAULT_HORIZON_BARS = 10


def _ensure_dir() -> None:
    os.makedirs(SHADOW_DIR, exist_ok=True)


def _append(path: str, row: Dict[str, Any]) -> bool:
    try:
        _ensure_dir()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, sort_keys=True) + "\n")
        return True
    except Exception:
        return False


def _read(path: str, limit: int = 20000) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return rows[-limit:]


def _signal_hash(payload: Dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(body).hexdigest()[:16]


def _spec_from_entry(entry: Dict[str, Any]):
    """Rebuild a StrategySpec from a registry row (params are what matters)."""
    from engine.v2 import strategy_lab

    return strategy_lab.StrategySpec(
        family=str(entry.get("family") or "BREAKOUT"),
        index_key=str(entry.get("index") or "NIFTY"),
        params=dict(entry.get("params") or {}),
        name=str(entry.get("name") or ""),
    )


def _live_state(df, spec) -> Dict[str, Any]:
    """Latest closed bar's rule state for one spec (no forward information)."""
    import pandas as pd
    from engine.v2 import strategy_lab

    if df is None or not len(df):
        return {}
    frame = df.copy()
    if not isinstance(frame.index, pd.DatetimeIndex):
        try:
            frame = frame.set_index(frame.columns[0])
        except Exception:
            return {}
    sig = strategy_lab._signals(frame, spec)
    last = len(frame) - 1
    if last < 1:
        return {}
    atr_val = sig["atr"].iloc[last]
    return {
        "bar_date": str(frame.index[last])[:10],
        "close": float(frame["Close"].astype(float).iloc[last]),
        "long_signal": bool(sig["long_entry"].iloc[last]),
        "short_signal": bool(sig["short_entry"].iloc[last]),
        "long_exit": bool(sig["long_exit"].iloc[last]),
        "short_exit": bool(sig["short_exit"].iloc[last]),
        "atr": None if pd.isna(atr_val) else float(atr_val),
    }


def record_day(index_key: str = "NIFTY") -> Dict[str, Any]:
    """Append today's signals for every validated spec on ``index_key``."""
    from engine.v2 import strategy_lab

    df = strategy_lab._frame(index_key)
    if df is None or not len(df):
        return {"status": "NO_DATA", "recorded": 0, "index": index_key}

    registry = strategy_lab.load_registry()
    entries = [e for e in (registry.get("strategies") or []) if e.get("validated")]
    recorded = skipped = 0
    for entry in entries:
        if str(entry.get("index") or "NIFTY").upper() != index_key.upper():
            continue
        try:
            spec = _spec_from_entry(entry)
            state = _live_state(df, spec)
            if not state:
                skipped += 1
                continue
            side = "LONG" if state["long_signal"] else ("SHORT" if state["short_signal"] else "FLAT")
            payload = {
                "ts": time.time(),
                "bar_date": state["bar_date"],
                "strategy_id": entry.get("name"),
                "family": entry.get("family"),
                "index": index_key,
                "side": side,
                "close": state["close"],
                "atr": state["atr"],
                "params": entry.get("params"),
                "horizon_bars": DEFAULT_HORIZON_BARS,
            }
            payload["signal_hash"] = _signal_hash({
                k: payload[k] for k in ("bar_date", "strategy_id", "side", "close", "params")
            })
            if _append(SIGNALS_PATH, payload):
                recorded += 1
        except Exception:
            skipped += 1
    return {"status": "OK", "index": index_key, "recorded": recorded,
            "skipped": skipped, "signals_path": SIGNALS_PATH}


def score_day(index_key: str = "NIFTY") -> Dict[str, Any]:
    """Score recorded signals whose horizon has now elapsed."""
    from engine.v2 import strategy_lab

    df = strategy_lab._frame(index_key)
    if df is None or not len(df):
        return {"status": "NO_DATA", "scored": 0}

    dates = [str(d)[:10] for d in df.index.tolist()]
    closes = [float(x) for x in df["Close"].astype(float).tolist()]
    pos = {d: i for i, d in enumerate(dates)}
    cost_bps = 12.0

    already = {(r.get("signal_hash"), r.get("bar_date")) for r in _read(SCORES_PATH)}
    scored = 0
    for row in _read(SIGNALS_PATH):
        if str(row.get("index", "")).upper() != index_key.upper():
            continue
        if (row.get("signal_hash"), row.get("bar_date")) in already:
            continue
        day = str(row.get("bar_date", ""))[:10]
        i = pos.get(day)
        if i is None:
            continue
        horizon = int(row.get("horizon_bars") or DEFAULT_HORIZON_BARS)
        j = i + horizon
        if j >= len(closes):
            continue
        entry = float(row.get("close") or closes[i])
        exit_ = closes[j]
        side = row.get("side")
        if side == "LONG":
            gross = (exit_ - entry) / entry
        elif side == "SHORT":
            gross = (entry - exit_) / entry
        else:
            gross = 0.0
        net = gross - 2 * cost_bps / 10_000.0
        _append(SCORES_PATH, {
            "scored_at": time.time(),
            "signal_hash": row.get("signal_hash"),
            "bar_date": day,
            "strategy_id": row.get("strategy_id"),
            "index": index_key,
            "side": side,
            "entry": entry,
            "exit": exit_,
            "horizon_bars": horizon,
            "gross_return_pct": round(gross * 100, 4),
            "net_return_pct": round(net * 100, 4),
        })
        scored += 1
    return {"status": "OK", "scored": scored, "scores_path": SCORES_PATH}


def reconciliation(min_signals: int = 5) -> Dict[str, Any]:
    """Live vs backtest comparison, with a decay verdict."""
    scores = [s for s in _read(SCORES_PATH) if s.get("side") in ("LONG", "SHORT")]
    if not scores:
        return {"status": "NO_FORWARD_RECORD", "scored": 0,
                "note": "run `python -m engine.v2 shadow --record` after each close, then --score"}

    by_strategy: Dict[str, List[float]] = {}
    for s in scores:
        by_strategy.setdefault(str(s.get("strategy_id")), []).append(float(s.get("net_return_pct", 0.0)))

    from engine.v2 import strategy_lab
    registry = {e.get("name"): e for e in (strategy_lab.load_registry().get("strategies") or [])}

    rows = []
    for sid, rets in by_strategy.items():
        if len(rets) < min_signals:
            continue
        mean = sum(rets) / len(rets)
        wins = sum(1 for r in rets if r > 0)
        metrics = (registry.get(sid) or {}).get("metrics") or {}
        bt_win = float(metrics.get("win_rate_pct") or 0.0)
        rows.append({
            "strategy_id": sid,
            "signals": len(rets),
            "live_mean_net_pct": round(mean, 4),
            "live_hit_rate_pct": round(100.0 * wins / len(rets), 1),
            "backtest_win_rate_pct": metrics.get("win_rate_pct"),
            "backtest_sharpe": metrics.get("sharpe"),
            "decayed": bool(mean < 0 or bt_win - 100.0 * wins / len(rets) > 20),
        })

    overall = [float(s.get("net_return_pct", 0.0)) for s in scores]
    return {
        "status": "OK",
        "signals_scored": len(overall),
        "live_mean_net_pct": round(sum(overall) / len(overall), 4) if overall else None,
        "strategies": rows,
        "decayed": [r["strategy_id"] for r in rows if r["decayed"]],
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(prog="python -m engine.v2.shadow")
    ap.add_argument("--record", action="store_true", help="append today's signals")
    ap.add_argument("--score", action="store_true", help="score signals whose horizon elapsed")
    ap.add_argument("--index", default="NIFTY")
    args = ap.parse_args()

    out: Dict[str, Any] = {}
    if args.record:
        out["record"] = record_day(args.index)
    if args.score:
        out["score"] = score_day(args.index)
    out["reconciliation"] = reconciliation()
    print(json.dumps(out, indent=2, default=str))
