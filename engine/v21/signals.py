"""
Signal engines behind the v2.1 **Suggestions** panel.

Four independent engines, each returning the same small contract so the panel
can list them side by side::

    {"engine", "label", "bias", "confidence", "detail", "metrics", "chart"}

1. :func:`hedge_regression` — the first attached image: regress a stock's returns
   on the index, read beta, R², the dollar amount to short as a hedge and how
   much the hedge removes from the position's swing.
2. :func:`stat_arb` — the second attached image: OLS hedge ratio on a 250-bar
   window, z-score of the spread, enter at |z| > 2, exit on sign flip.
3. :func:`chart_context` — the v2.1 pattern taxonomy: liquidity sweep, breakout,
   retest/pullback, mean reversion, each mapped to *accept / reject / defend*.
4. :func:`macro_priority` — gold, oil and bond-yield headlines with the
   "yields up ⇒ inflation up" linkage the terminal is required to carry.

All functions are pure numpy/pandas: no network here, so they are unit-tested and
can run on a background tick.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

# ── 1. Hedge predictor (image #1) ───────────────────────────────────────────


def hedge_regression(market_returns: Sequence[float],
                     stock_returns: Sequence[float],
                     capital: float = 1_000_000.0) -> Dict[str, Any]:
    """
    OLS hedge of a stock against the index, in the image's five steps.

    Steps: (1) fit the line, (2) share explained (R²), (3) stock minus beta·market,
    (4) slope/R²/dollars to short, (5) the reduction in dollar swing.
    """
    m = np.asarray([float(x) for x in market_returns if pd.notna(x)], dtype=float)
    s = np.asarray([float(x) for x in stock_returns if pd.notna(x)], dtype=float)
    n = min(len(m), len(s))
    if n < 30:
        return {"engine": "hedge", "available": False,
                "reason": f"need >= 30 aligned returns, got {n}"}
    m, s = m[-n:], s[-n:]

    beta, alpha = np.polyfit(m, s, 1)
    resid = s - (alpha + beta * m)
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((s - s.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    hedge_notional = float(beta) * capital
    raw_swing = float(np.std(s, ddof=1))
    hedged_swing = float(np.std(resid, ddof=1))
    reduction = (1.0 - hedged_swing / raw_swing) * 100.0 if raw_swing > 0 else 0.0

    if beta > 1.2:
        bias, label = "HEDGE", "high beta — short index futures against it"
    elif beta < 0.6:
        bias, label = "UNDER-HEDGED", "low beta — index hedge adds little protection"
    else:
        bias, label = "BALANCED", "market-like beta — hedge roughly 1:1"

    return {
        "engine": "hedge",
        "available": True,
        "label": f"Hedge β {beta:.2f} · R² {r2:.2f}",
        "bias": bias,
        "confidence": round(min(0.9, 0.45 + r2 * 0.5), 2),
        "detail": (f"Short ₹{hedge_notional/1e5:,.1f} lakh of index exposure to hedge "
                   f"₹{capital/1e5:,.0f} lakh of stock; residual swing falls "
                   f"{reduction:.0f}% ({raw_swing*1e4:.0f} → {hedged_swing*1e4:.0f} bps/day)."),
        "metrics": {
            "beta": round(float(beta), 4),
            "alpha_daily": round(float(alpha), 6),
            "r2": round(r2, 4),
            "hedge_notional": round(hedge_notional, 2),
            "raw_swing_bps": round(raw_swing * 1e4, 2),
            "hedged_swing_bps": round(hedged_swing * 1e4, 2),
            "swing_reduction_pct": round(reduction, 2),
            "n_obs": n,
        },
        "note": label,
    }


# ── 2. Statistical arbitrage (image #2) ─────────────────────────────────────


def stat_arb(prices_a: Sequence[float], prices_b: Sequence[float],
             lookback: int = 250, entry_z: float = 2.0,
             name_a: str = "A", name_b: str = "B") -> Dict[str, Any]:
    """
    Pairs trade exactly as in the attached snippet: hedge ratio from the first
    ``lookback`` bars, spread z-score, enter at |z| > ``entry_z``, exit when the
    z-score flips sign, P&L in spread units.

    Returns the historical trades *and* the live signal for the last bar.
    """
    a = np.asarray([float(x) for x in prices_a], dtype=float)
    b = np.asarray([float(x) for x in prices_b], dtype=float)
    n = min(len(a), len(b))
    if n < lookback + 30:
        return {"engine": "statarb", "available": False,
                "reason": f"need >= {lookback + 30} aligned bars, got {n}"}
    a, b = a[-n:], b[-n:]

    beta = float(np.polyfit(b[:lookback], a[:lookback], 1)[0])
    spread = a - beta * b
    mu, sd = float(spread[:lookback].mean()), float(spread[:lookback].std(ddof=1))
    if sd <= 0:
        return {"engine": "statarb", "available": False, "reason": "degenerate spread"}

    z = (spread - mu) / sd
    pos, entry = 0, 0
    trades: List[Dict[str, Any]] = []
    for t in range(lookback, n):
        if pos == 0 and abs(z[t]) > entry_z:
            pos, entry = -int(np.sign(z[t])), t
        elif pos != 0 and np.sign(z[t]) == pos:
            trades.append({
                "entry_bar": int(entry),
                "exit_bar": int(t),
                "side": "LONG_SPREAD" if pos > 0 else "SHORT_SPREAD",
                "pnl": float(pos * (spread[t] - spread[entry])),
                "entry_z": round(float(z[entry]), 2),
                "exit_z": round(float(z[t]), 2),
            })
            pos = 0

    pnls = [tr["pnl"] for tr in trades]
    wins = sum(1 for p in pnls if p > 0)
    z_now = float(z[-1])
    if abs(z_now) > entry_z:
        bias = "LONG SPREAD" if z_now < 0 else "SHORT SPREAD"
        label = f"{bias} ({name_a}/{name_b}) · z={z_now:.2f}"
        detail = (f"Spread is {abs(z_now):.1f}σ from its mean; own {name_a} and short "
                  f"{beta:.2f}× {name_b}." if z_now < 0 else
                  f"Spread is {abs(z_now):.1f}σ above its mean; short {name_a} and own "
                  f"{beta:.2f}× {name_b}.")
        confidence = round(min(0.85, 0.4 + abs(z_now) / 8), 2)
    else:
        bias = "FLAT"
        label = f"no trade · z={z_now:.2f}"
        detail = f"Spread inside ±{entry_z:.0f}σ; wait for a dislocation."
        confidence = 0.25

    return {
        "engine": "statarb",
        "available": True,
        "label": label,
        "bias": bias,
        "confidence": confidence,
        "detail": detail,
        "metrics": {
            "beta": round(beta, 4),
            "z_now": round(z_now, 3),
            "mu": round(mu, 3),
            "sd": round(sd, 3),
            "trades": len(trades),
            "wins": wins,
            "hit_rate_pct": round(100.0 * wins / len(pnls), 1) if pnls else None,
            "total_pnl_spread_units": round(float(sum(pnls)), 3),
            "entry_z": entry_z,
        },
        "chart": {
            "type": "zscore",
            "z": [round(float(v), 4) for v in z[lookback:].tolist()],
            "bands": [-entry_z, entry_z],
            "x_label": "bars since lookback",
            "y_label": "spread z",
        },
        "trades": trades[-20:],
    }


# ── 3. Chart context taxonomy ───────────────────────────────────────────────

_PATTERNS = {
    "liquidity_sweep": ("liquidity sweep / S&D", "rejecting the price",
                        "fade the wick back into range"),
    "breakout": ("breakout", "accepting + expanding the price",
                 "trade with the expansion, not against it"),
    "retest": ("retest / pullback", "defending the price",
               "join the defended level with a tight invalidation"),
    "mean_reversion": ("mean reversion", "rejecting and returning to value",
                       "fade the stretch back to the mean"),
}


def chart_context(df: pd.DataFrame, lookback: int = 20) -> Dict[str, Any]:
    """
    Classify the last bars into one of the four behaviours the terminal must
    speak in: sweep (reject), breakout (accept), retest (defend), reversion.

    Uses only OHLCV: close vs prior range, wick rejection, volume expansion and
    distance from the mean.
    """
    if df is None or len(df) < lookback + 5:
        return {"engine": "pattern", "available": False, "reason": "not enough bars"}
    d = df.tail(max(lookback + 5, 60)).reset_index(drop=True)
    o, h, l, c = (d[x].astype(float) for x in ("open", "high", "low", "close"))
    v = d["volume"].astype(float) if "volume" in d.columns else pd.Series(np.ones(len(d)))
    last = -1

    hi_n = float(h.iloc[:-1].tail(lookback).max())
    lo_n = float(l.iloc[:-1].tail(lookback).min())
    rng = max(hi_n - lo_n, 1e-9)
    close = float(c.iloc[last])
    body = abs(close - float(o.iloc[last]))
    upper_wick = float(h.iloc[last]) - max(close, float(o.iloc[last]))
    lower_wick = min(close, float(o.iloc[last])) - float(l.iloc[last])
    vol_ratio = float(v.iloc[last] / max(v.tail(lookback).mean(), 1e-9))
    mean20 = float(c.tail(20).mean())
    sd20 = float(c.tail(20).std(ddof=1) or 0.0)
    z20 = (close - mean20) / sd20 if sd20 > 0 else 0.0

    pattern, action = "mean_reversion", ""
    reason = ""
    if close > hi_n and vol_ratio >= 1.2:
        pattern = "breakout"
        reason = f"close {close:.2f} above {lookback}-bar high {hi_n:.2f} on {vol_ratio:.1f}× volume"
    elif float(h.iloc[last]) > hi_n and close < hi_n and upper_wick > body:
        pattern = "liquidity_sweep"
        reason = f"wick through {hi_n:.2f} rejected, close back inside the range"
    elif float(l.iloc[last]) < lo_n and close > lo_n and lower_wick > body:
        pattern = "liquidity_sweep"
        reason = f"wick through {lo_n:.2f} rejected, close back inside the range"
    elif hi_n > 0 and abs(close - hi_n) / rng < 0.03 and vol_ratio < 1.0:
        pattern = "retest"
        reason = f"price holding within 3% of the {lookback}-bar high {hi_n:.2f} on light volume"
    else:
        reason = f"price {z20:+.2f}σ from its 20-bar mean ({mean20:.2f})"

    name, behaviour, playbook = _PATTERNS[pattern]
    return {
        "engine": "pattern",
        "available": True,
        "label": f"{name} · {behaviour}",
        "bias": playbook,
        "pattern": pattern,
        "pattern_name": name,
        "behaviour": behaviour,
        "confidence": 0.55 if pattern == "mean_reversion" else 0.65,
        "detail": reason.capitalize() + ".",
        "metrics": {
            "close": round(close, 2),
            "range_high": round(hi_n, 2),
            "range_low": round(lo_n, 2),
            "volume_ratio": round(vol_ratio, 2),
            "z20": round(z20, 2),
        },
        "playbook": playbook,
    }


# ── 3b. Consensus across engines ────────────────────────────────────────────

# Engines speak different vocabularies; only the leading word carries direction.
_LONG = {"BUY", "LONG", "BREAKOUT"}
_SHORT = {"SELL", "SHORT", "FADE"}


def _direction(bias: str) -> int:
    """+1 / -1 / 0 from the first word of an engine's bias label."""
    words = str(bias or "").upper().split()
    word = words[0] if words else ""
    return 1 if word in _LONG else -1 if word in _SHORT else 0


def consensus(ideas: Sequence[Dict[str, Any]], min_engines: int = 2) -> Dict[str, Any]:
    """
    Roll every engine's view into one labelled, falsifiable trade stance.

    Direction is the first word of each bias, so "LONG SPREAD" votes long and
    "HEDGE" / "BALANCED" / "WAIT" abstain. Agreement caps confidence: three of
    three engines at 0.6 is 0.6, two of three is 0.4. A tie is reported as MIXED
    rather than broken by a heuristic.
    """
    votes = [{"engine": i.get("engine", "?"), "label": i.get("label", ""),
              "bias": str(i.get("bias", "")).upper(),
              "direction": _direction(i.get("bias")),
              "confidence": float(i.get("confidence") or 0.0)}
             for i in ideas if i.get("available")]

    if len(votes) < min_engines:
        return {"available": False, "votes": votes,
                "reason": f"need >= {min_engines} engines, got {len(votes)}"}

    longs = sum(1 for v in votes if v["direction"] > 0)
    shorts = sum(1 for v in votes if v["direction"] < 0)
    side = "BUY" if longs > shorts else "SELL" if shorts > longs else "WAIT"
    bias = {"BUY": "LONG", "SELL": "SHORT"}.get(side, "MIXED")
    winner = max(longs, shorts)
    confidence = round(min(0.9, sum(v["confidence"] for v in votes) / len(votes)
                           * winner / len(votes)), 2)
    split = f"{longs} long / {shorts} short / {len(votes) - longs - shorts} flat"
    named = ", ".join(f"{v['engine']}={v['bias'] or 'n/a'}" for v in votes)
    label = f"CONSENSUS {bias} · {split}" if side != "WAIT" else f"MIXED · {split}"
    detail = (f"No majority ({split}); votes: {named}." if side == "WAIT" else
              f"{winner}/{len(votes)} engines point {bias.lower()} ({split}); votes: {named}.")
    return {"available": True, "engine": "consensus", "label": label, "bias": bias,
            "action": side, "confidence": confidence, "detail": detail, "votes": votes,
            "longs": longs, "shorts": shorts, "neutral": len(votes) - longs - shorts,
            "gex_label": next((v["label"] for v in votes if v["engine"] == "gex"), ""),
            "invalidation": "any two engines flipping direction, or the GEX regime changing sign"}


# ── 4. Macro priority headlines ─────────────────────────────────────────────


def macro_priority(gold_change_pct: Optional[float],
                   oil_change_pct: Optional[float],
                   yield_10y: Optional[float],
                   yield_10y_prev: Optional[float],
                   inflation_pct: Optional[float] = None,
                   swing_threshold_pct: float = 1.5) -> List[Dict[str, Any]]:
    """
    Gold / oil / bond-yield priority logic.

    Gold only becomes a *top* headline on a large swing-frame move (default
    1.5%); oil always reports; yields carry the required linkage
    *yields up ⇒ inflation up*, with the real yield spelled out when inflation
    is known.
    """
    out: List[Dict[str, Any]] = []

    if gold_change_pct is not None:
        big = abs(float(gold_change_pct)) >= swing_threshold_pct
        out.append({
            "asset": "GOLD",
            "priority": "TOP" if big else "low",
            "value": round(float(gold_change_pct), 2),
            "message": (f"Gold moved {gold_change_pct:+.2f}% — a swing-frame outlier, "
                        f"treat as a risk headline." if big else
                        f"Gold {gold_change_pct:+.2f}% — inside the noise band, ignore."),
            "rule": f"|move| >= {swing_threshold_pct}%",
        })

    if oil_change_pct is not None:
        out.append({
            "asset": "OIL",
            "priority": "TOP" if abs(float(oil_change_pct)) >= 2.0 else "medium",
            "value": round(float(oil_change_pct), 2),
            "message": f"Brent/WTI {oil_change_pct:+.2f}% — feeds directly into India CPI.",
            "rule": "always report; >= 2% is top priority",
        })

    if yield_10y is not None and yield_10y_prev is not None:
        d_bp = (float(yield_10y) - float(yield_10y_prev)) * 100.0
        out.append({
            "asset": "BOND_YIELD_10Y",
            "priority": "TOP" if abs(d_bp) >= 5 else "medium",
            "value": round(float(yield_10y), 3),
            "message": (f"10Y yield {d_bp:+.1f}bp. Yields up ⇒ inflation expectations up ⇒ "
                        f"rate-sensitive equities and long duration under pressure."
                        if d_bp > 0 else
                        f"10Y yield {d_bp:+.1f}bp. Yields down ⇒ disinflation/demand fear; "
                        f"duration and gold bid."),
            "rule": "yield up > inflation up (required v2.1 linkage)",
            "real_yield": (round(float(yield_10y) - float(inflation_pct), 2)
                           if inflation_pct is not None else None),
        })
    return out
