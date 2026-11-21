"""
ZERO Engine v2.0 — Market Structure & Whale Flow Study
======================================================

The desk asked the engine to become a *student* of market structure, price
action, risk management and trade psychology — specifically to recognise when
large funds slice a big order into many child orders and push price in one
direction while liquidity is rebuilt or consumed.

What this module can honestly do from daily OHLCV:

* **Participation** — volume z-score and relative volume (RVOL).
* **Absorption** — heavy volume with a compressed range: a large passive
  buyer/seller is soaking up aggression.
* **Sliced execution (iceberg)** — several consecutive bars in the same
  direction, overlapping ranges, rising volume: the footprint of a parent
  order worked by an execution algo.
* **Liquidity sweep** — a wick through a prior swing that closes back inside
  the range: stops were harvested, then the level was defended.
* **Initiative aggression** — expansion bar on extreme volume: a whale taking
  liquidity rather than providing it.
* **Wyckoff phase** — accumulation / markup / distribution / markdown from the
  relationship between range position and volume trend.

It cannot see the order book. Every finding carries an explicit ``limit`` note
so the output is never mistaken for tape data. Where true depth is required,
``exchange_depth_available`` is reported as ``False``.
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "2.0"

# Detection thresholds (tunable, documented so a desk can justify an alert).
VOLUME_Z_ABSORPTION = 2.0
RANGE_COMPRESSION = 0.60      # bar range <= 60% of ATR
SLICE_MIN_BARS = 3
SWEEP_WICK_ATR = 1.20
INITIATIVE_VOLUME_Z = 2.50
INITIATIVE_BODY_ATR = 1.20


def _flatten(frame) -> Optional[Any]:
    if frame is None or getattr(frame, "empty", True):
        return None
    try:
        out = frame.copy()
        if hasattr(out.columns, "levels"):
            out.columns = [str(c[0]) for c in out.columns]
        out = out.loc[:, ~out.columns.duplicated()]
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col in out.columns:
                out[col] = out[col].astype(float)
        if "Close" not in out.columns:
            return None
        out = out.dropna(subset=["Close"])
        return out if len(out) >= 15 else None
    except Exception:
        return None


def _atr(df, period: int = 14) -> float:
    try:
        high, low, close = df["High"], df["Low"], df["Close"].shift(1)
        tr = (high - low).to_frame("a")
        tr["b"] = (high - close).abs()
        tr["c"] = (low - close).abs()
        val = tr.max(axis=1).rolling(period).mean().iloc[-1]
        return float(val)
    except Exception:
        return float("nan")


def _volume_z(df, period: int = 20) -> List[float]:
    try:
        vol = df["Volume"].astype(float)
        mean = vol.rolling(period).mean()
        std = vol.rolling(period).std(ddof=0)
        z = (vol - mean) / std.replace(0, float("nan"))
        return [0.0 if (v is None or not math.isfinite(float(v))) else float(v) for v in z]
    except Exception:
        return [0.0] * len(df)


def detect_events(df) -> List[Dict[str, Any]]:
    """Return every structural event found in the last ~60 bars, newest first."""
    frame = _flatten(df)
    if frame is None:
        return []
    atr = _atr(frame)
    if not math.isfinite(atr) or atr <= 0:
        return []
    frame = frame.tail(80).copy()
    z = _volume_z(frame)
    events: List[Dict[str, Any]] = []
    dates = [str(d)[:10] for d in frame.index]

    body = (frame["Close"] - frame["Open"]).abs()
    rng = (frame["High"] - frame["Low"]).replace(0, float("nan"))

    # 1. Absorption: huge volume, tiny range.
    for i in range(len(frame)):
        if z[i] >= VOLUME_Z_ABSORPTION and float(rng.iloc[i]) <= RANGE_COMPRESSION * atr:
            close_pos = _close_position(frame.iloc[i], atr)
            bias = "buyer absorbing supply" if close_pos > 0.5 else "seller absorbing demand"
            events.append({
                "type": "ABSORPTION",
                "date": dates[i],
                "bar": i,
                "confidence": round(min(1.0, z[i] / 4.0), 2),
                "bias": bias,
                "note": (
                    f"Volume {z[i]:.1f}σ with a {float(rng.iloc[i]):.0f}-pt range "
                    f"(ATR {atr:.0f}). Close at {close_pos:.0%} of the bar. "
                    f"A passive desk is {bias}."
                ),
            })

    # 2. Sliced execution / iceberg: same-direction closes, overlapping ranges,
    #    rising volume — what a parent order looks like after a slicing algo.
    for i in range(SLICE_MIN_BARS - 1, len(frame)):
        window = frame.iloc[i - SLICE_MIN_BARS + 1:i + 1]
        closes = window["Close"].tolist()
        ups = sum(1 for a, b in zip(closes, closes[1:]) if b > a)
        downs = sum(1 for a, b in zip(closes, closes[1:]) if b < a)
        if ups < SLICE_MIN_BARS - 1 and downs < SLICE_MIN_BARS - 1:
            continue
        overlaps = all(
            float(window["Low"].iloc[k + 1]) <= float(window["High"].iloc[k])
            and float(window["High"].iloc[k + 1]) >= float(window["Low"].iloc[k])
            for k in range(len(window) - 1)
        )
        vols = window["Volume"].astype(float).tolist()
        rising_vol = len(vols) >= 3 and vols[-1] > vols[0]
        if overlaps and rising_vol and max(z[i - SLICE_MIN_BARS + 1:i + 1]) > 0.8:
            direction = "accumulation" if ups > downs else "distribution"
            events.append({
                "type": "SLICED_EXECUTION",
                "date": dates[i],
                "bar": i,
                "confidence": round(min(1.0, 0.45 + 0.1 * (SLICE_MIN_BARS)), 2),
                "bias": direction,
                "note": (
                    f"{SLICE_MIN_BARS} overlapping closes moving "
                    f"{'up' if ups > downs else 'down'} on rising volume. "
                    "Consistent with a parent order worked by a slicing algo "
                    f"({direction} footprint)."
                ),
            })
            break  # one representative event is enough

    # 3. Liquidity sweep: wick beyond the prior 10-bar extreme, close back in.
    for i in range(10, len(frame)):
        prior_high = float(frame["High"].iloc[i - 10:i].max())
        prior_low = float(frame["Low"].iloc[i - 10:i].min())
        hi, lo, close = float(frame["High"].iloc[i]), float(frame["Low"].iloc[i]), float(frame["Close"].iloc[i])
        if hi > prior_high + SWEEP_WICK_ATR * 0.25 * atr and close < prior_high:
            events.append({
                "type": "LIQUIDITY_SWEEP",
                "date": dates[i],
                "bar": i,
                "confidence": 0.7,
                "bias": "bearish sweep (stops above, level rejected)",
                "note": (
                    f"Traded {hi - prior_high:.0f} pts above the 10-bar high then "
                    f"closed {prior_high - close:.0f} pts back inside. Buy-side "
                    "liquidity was harvested and the level was defended."
                ),
            })
        elif lo < prior_low - SWEEP_WICK_ATR * 0.25 * atr and close > prior_low:
            events.append({
                "type": "LIQUIDITY_SWEEP",
                "date": dates[i],
                "bar": i,
                "confidence": 0.7,
                "bias": "bullish sweep (stops below, level reclaimed)",
                "note": (
                    f"Traded {prior_low - lo:.0f} pts below the 10-bar low then "
                    f"closed {close - prior_low:.0f} pts back inside. Sell-side "
                    "liquidity was harvested and the level was reclaimed."
                ),
            })

    # 4. Initiative aggression: expansion bar on extreme volume.
    for i in range(len(frame)):
        if z[i] >= INITIATIVE_VOLUME_Z and float(body.iloc[i]) >= INITIATIVE_BODY_ATR * atr:
            direction = "buyers" if float(frame["Close"].iloc[i]) > float(frame["Open"].iloc[i]) else "sellers"
            events.append({
                "type": "INITIATIVE_AGGRESSION",
                "date": dates[i],
                "bar": i,
                "confidence": round(min(1.0, z[i] / 5.0), 2),
                "bias": f"{direction} taking liquidity",
                "note": (
                    f"{z[i]:.1f}σ volume with a {float(body.iloc[i]):.0f}-pt body "
                    f"({float(body.iloc[i]) / atr:.1f}× ATR). {direction.title()} "
                    "crossed the spread and paid up — this is a taker, not a provider."
                ),
            })

    events.sort(key=lambda e: (e["bar"], e["confidence"]), reverse=True)
    return events[:12]


def _close_position(bar, atr: float) -> float:
    try:
        hi, lo, close = float(bar["High"]), float(bar["Low"]), float(bar["Close"])
        if hi <= lo:
            return 0.5
        return max(0.0, min(1.0, (close - lo) / (hi - lo)))
    except Exception:
        return 0.5


def classify_phase(df) -> Dict[str, Any]:
    """Coarse Wyckoff phase from range position + volume trend + slope."""
    frame = _flatten(df)
    if frame is None:
        return {"phase": "UNKNOWN", "confidence": 0.0, "note": "insufficient history"}
    frame = frame.tail(40)
    close = frame["Close"].astype(float)
    try:
        hi, lo = float(close.max()), float(close.min())
        pos = (float(close.iloc[-1]) - lo) / (hi - lo) if hi > lo else 0.5
        slope = (float(close.iloc[-1]) - float(close.iloc[0])) / max(1e-9, float(close.iloc[0]))
        vol = frame["Volume"].astype(float)
        vol_trend = (float(vol.tail(10).mean()) - float(vol.head(10).mean())) / max(1e-9, float(vol.head(10).mean()))
    except Exception:
        return {"phase": "UNKNOWN", "confidence": 0.0, "note": "malformed frame"}

    if slope > 0.02 and pos > 0.6:
        phase, mood = "MARKUP", "trend confidence, late chasers enter"
    elif slope < -0.02 and pos < 0.4:
        phase, mood = "MARKDOWN", "capitulation risk, rallies get sold"
    elif abs(slope) <= 0.02 and vol_trend < 0:
        phase, mood = "ACCUMULATION", "boredom and disbelief — large buyers work quietly"
    elif abs(slope) <= 0.02 and vol_trend > 0:
        phase, mood = "DISTRIBUTION", "optimism with heavy supply — rallies stall"
    else:
        phase, mood = "TRANSITION", "no edge; wait for a range break"

    return {
        "phase": phase,
        "range_position": round(pos, 3),
        "slope_40d": round(slope, 4),
        "volume_trend": round(vol_trend, 4),
        "confidence": 0.6,
        "psychology": mood,
        "note": (
            f"Price is at {pos:.0%} of the 40-bar range with a {slope:+.2%} slope "
            f"and volume trend {vol_trend:+.0%}. Crowd mood: {mood}."
        ),
    }


def key_levels(df) -> Dict[str, Any]:
    """Structural levels a desk would mark before the open."""
    frame = _flatten(df)
    if frame is None:
        return {}
    try:
        atr = _atr(frame)
        close = frame["Close"].astype(float)
        last = float(close.iloc[-1])
        prior = frame.iloc[-2] if len(frame) >= 2 else frame.iloc[-1]
        highs = [float(frame["High"].tail(n).max()) for n in (5, 20, 60) if len(frame) >= n]
        lows = [float(frame["Low"].tail(n).min()) for n in (5, 20, 60) if len(frame) >= n]
        return {
            "last_close": round(last, 2),
            "atr14": round(atr, 2) if math.isfinite(atr) else None,
            "prior_high": round(float(prior["High"]), 2),
            "prior_low": round(float(prior["Low"]), 2),
            "prior_close": round(float(prior["Close"]), 2),
            "swing_highs": [round(h, 2) for h in highs],
            "swing_lows": [round(l, 2) for l in lows],
            "range_60d": round(float(frame["High"].tail(60).max()) - float(frame["Low"].tail(60).min()), 2),
        }
    except Exception:
        return {}


def study(df, name: str = "INDEX") -> Dict[str, Any]:
    """Full structural study for one index."""
    frame = _flatten(df)
    if frame is None:
        return {
            "schema": SCHEMA_VERSION, "index": name, "status": "NO_DATA",
            "events": [], "phase": {"phase": "UNKNOWN"}, "levels": {},
            "narrative": [f"{name}: no usable OHLCV history was returned."],
            "limits": ["Detection needs >= 15 daily bars."],
        }
    events = detect_events(frame)
    phase = classify_phase(frame)
    levels = key_levels(frame)
    atr = levels.get("atr14") or 0.0

    counts: Dict[str, int] = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1

    narrative: List[str] = []
    narrative.append(
        f"{name} is in a {phase['phase']} phase — {phase.get('psychology', '')}."
    )
    if atr:
        narrative.append(
            f"ATR(14) is {atr:,.0f} pts, so a normal day's range is about "
            f"{atr:,.0f} pts and a 1.5× ATR stop is {atr * 1.5:,.0f} pts."
        )
    for e in events[:4]:
        narrative.append(f"{e['date']}: {e['type']} — {e['note']}")
    if not events:
        narrative.append(
            "No absorption, slicing, sweep or initiative footprint in the last 80 bars. "
            "Absence of a footprint is itself information: no large participant is working size here."
        )

    return {
        "schema": SCHEMA_VERSION,
        "index": name,
        "status": "OK",
        "asof": _dt.datetime.now().isoformat(timespec="seconds"),
        "bars": int(len(frame)),
        "last_bar": str(frame.index[-1])[:10],
        "event_counts": counts,
        "events": events,
        "phase": phase,
        "levels": levels,
        "narrative": narrative,
        "risk_notes": [
            "Stop distance is derived from ATR, not from conviction.",
            "Size so that (entry - stop) × quantity is a fixed fraction of capital.",
            "A sweep event invalidates the level it swept; do not re-enter on the same thesis.",
        ],
        "limits": [
            "Detections use daily OHLCV, not order-book depth.",
            "exchange_depth_available: False — true whale identification needs tick/Level-2 data.",
            "Sliced-execution detection is a footprint heuristic, not proof of a single parent order.",
        ],
        "exchange_depth_available": False,
    }
