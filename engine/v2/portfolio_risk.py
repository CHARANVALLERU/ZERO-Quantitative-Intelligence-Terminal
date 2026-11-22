"""
engine.v2.portfolio_risk — risk of the *book*, not of each strategy.
====================================================================

Each validated strategy was reported with its own 12% drawdown limit. Four
strategies that each respect 12% can still produce a 30% book drawdown if they
draw down together, and nothing in the old pipeline measured that. This module
turns per-strategy trade lists into one calendar-aligned portfolio return
series and reports the numbers that actually bind:

* portfolio max drawdown / volatility / Sharpe, with an explicit limit;
* the correlation matrix the allocation relies on;
* concentration (HHI and the largest single-strategy weight);
* risk contribution per strategy (marginal variance), not just capital weight.

No network, no pandas required for the maths (lists in, dicts out); pandas is
used only when it is already available for date handling.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "MAX_PORTFOLIO_DD_PCT",
    "MAX_SINGLE_STRATEGY_WEIGHT",
    "daily_returns",
    "align",
    "correlation_matrix",
    "inverse_vol_weights",
    "risk_contributions",
    "book_risk",
    "gate",
]

MAX_PORTFOLIO_DD_PCT = 15.0
MAX_SINGLE_STRATEGY_WEIGHT = 0.40
MIN_OVERLAP_DAYS = 20


def _parse_day(value: Any) -> Optional[str]:
    """Normalise a trade date to YYYY-MM-DD; None when unparseable."""
    if value is None:
        return None
    text = str(value)
    if not text or text.startswith(("bar_", "Bar_", "idx_")):
        return None
    # Fast path: ISO-like
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    try:
        import pandas as pd
        ts = pd.to_datetime(text, errors="coerce")
        if ts is None or (hasattr(ts, "isna") and bool(ts is pd.NaT)):
            return None
        return ts.strftime("%Y-%m-%d")
    except Exception:
        return None


def daily_returns(trades: Sequence[Dict[str, Any]], capital: float = 1_000_000.0,
                  require_calendar: bool = True) -> Dict[str, float]:
    """
    Collapse a trade list into {date: return_fraction}.

    Returns are booked on the exit date. When the blotter has no usable dates
    and ``require_calendar`` is set, the result is empty: an unalignable series
    must not be silently treated as aligned.
    """
    out: Dict[str, float] = {}
    usable = 0
    for t in trades or []:
        day = _parse_day(t.get("exit_date") or t.get("exit_time") or t.get("entry_date"))
        if day is None:
            continue
        usable += 1
        try:
            pnl = float(t.get("pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        out[day] = out.get(day, 0.0) + pnl / max(1e-9, capital)
    if require_calendar and not usable:
        return {}
    return dict(sorted(out.items()))


def align(series_by_id: Dict[str, Dict[str, float]]
          ) -> Tuple[List[str], Dict[str, List[float]]]:
    """Union calendar + per-id returns with zeros on dates the id was flat."""
    dates = sorted({d for s in series_by_id.values() for d in s})
    return dates, {k: [float(s.get(d, 0.0)) for d in dates] for k, s in series_by_id.items()}


def _std(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _corr(a: Sequence[float], b: Sequence[float], min_overlap: int = MIN_OVERLAP_DAYS):
    n = min(len(a), len(b))
    if n < min_overlap:
        return None, n
    x, y = a[-n:], b[-n:]
    sx, sy = _std(x), _std(y)
    if sx <= 1e-12 or sy <= 1e-12:
        return 0.0, n
    mx, my = _mean(x), _mean(y)
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / (n - 1)
    return cov / (sx * sy), n


def correlation_matrix(series_by_id: Dict[str, Dict[str, float]],
                       min_overlap: int = MIN_OVERLAP_DAYS) -> Dict[str, Any]:
    dates, aligned = align(series_by_id)
    ids = list(aligned.keys())
    corr: Dict[str, Dict[str, float]] = {i: {} for i in ids}
    overlap: Dict[str, Dict[str, int]] = {i: {} for i in ids}
    for i in ids:
        corr[i][i] = 1.0
        overlap[i][i] = len(dates)
        for j in ids:
            if j <= i:
                continue
            c, n = _corr(aligned[i], aligned[j], min_overlap)
            value = 1.0 if c is None else round(float(c), 3)
            corr[i][j] = corr[j][i] = value
            overlap[i][j] = overlap[j][i] = n
    return {"ids": ids, "corr": corr, "overlap": overlap, "dates": len(dates)}


def inverse_vol_weights(aligned: Dict[str, List[float]],
                        cap: float = MAX_SINGLE_STRATEGY_WEIGHT) -> Dict[str, float]:
    """Inverse-volatility weights, capped, with redistribution of the excess."""
    vols = {k: _std(v) for k, v in aligned.items()}
    valid = {k: v for k, v in vols.items() if v > 1e-12}
    if not valid:
        n = max(1, len(aligned))
        return {k: 1.0 / n for k in aligned}
    inv = {k: 1.0 / v for k, v in valid.items()}
    total = sum(inv.values())
    w = {k: v / total for k, v in inv.items()}
    for k in aligned:
        w.setdefault(k, 0.0)
    for _ in range(8):
        over = {k: v - cap for k, v in w.items() if v > cap + 1e-12}
        if not over:
            break
        excess = sum(over.values())
        for k in over:
            w[k] = cap
        room = {k: cap - v for k, v in w.items() if v < cap - 1e-12}
        room_total = sum(room.values())
        if room_total <= 1e-12:
            break
        for k, r in room.items():
            w[k] += excess * (r / room_total)
    return w


def risk_contributions(weights: Dict[str, float], corr: Dict[str, Dict[str, float]],
                       vols: Dict[str, float]) -> Dict[str, float]:
    """
    Percentage contribution to portfolio variance per strategy.

    ``RC_i = w_i * (Σ_j w_j σ_i σ_j ρ_ij) / σ_p^2``. A strategy can be a small
    capital weight and still dominate the book's risk; this is the number that
    shows it.
    """
    ids = list(weights.keys())
    cov: Dict[str, Dict[str, float]] = {}
    for i in ids:
        cov[i] = {}
        for j in ids:
            rho = float(corr.get(i, {}).get(j, 0.0))
            cov[i][j] = vols.get(i, 0.0) * vols.get(j, 0.0) * rho
    port_var = sum(weights[i] * weights[j] * cov[i][j] for i in ids for j in ids)
    if port_var <= 1e-18:
        return {i: 0.0 for i in ids}
    return {
        i: round(100.0 * weights[i] * sum(weights[j] * cov[i][j] for j in ids) / port_var, 2)
        for i in ids
    }


def _max_drawdown(returns: Iterable[float]) -> float:
    eq = 1.0
    peak = 1.0
    worst = 0.0
    for r in returns:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        if peak > 0:
            worst = max(worst, (peak - eq) / peak)
    return worst


def book_risk(runs: Dict[str, Any], capital: float = 1_000_000.0,
              weights: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """
    Aggregate per-strategy backtest results into one book.

    ``runs`` maps strategy id to either a backtest result dict (with
    ``trades``) or a plain trade list. Strategies whose blotter carries no
    calendar are reported as ``unalignable`` and excluded from the book maths —
    they cannot be correlated with anything.
    """
    series: Dict[str, Dict[str, float]] = {}
    unalignable: List[str] = []
    for sid, run in (runs or {}).items():
        trades = run.get("trades") if isinstance(run, dict) else run
        s = daily_returns(trades or [], capital=capital)
        if not s:
            unalignable.append(sid)
            continue
        series[sid] = s

    if not series:
        return {"status": "NO_ALIGNED_DATA", "unalignable": unalignable,
                "max_drawdown_pct": None, "gate_passed": False}

    dates, aligned = align(series)
    cm = correlation_matrix(series)
    vols = {k: _std(v) for k, v in aligned.items()}
    w = dict(weights or inverse_vol_weights(aligned))

    port = [sum(w.get(k, 0.0) * aligned[k][i] for k in aligned) for i in range(len(dates))]
    sd = _std(port)
    mu = _mean(port)
    sharpe = (mu / sd * math.sqrt(252)) if sd > 1e-12 else 0.0
    dd = _max_drawdown(port) * 100.0
    equity = 1.0
    for r in port:
        equity *= (1.0 + r)

    wsum = sum(w.values()) or 1.0
    hhi = sum((v / wsum) ** 2 for v in w.values())
    largest = max(w.values()) / wsum if w else 0.0
    rc = risk_contributions({k: v / wsum for k, v in w.items()}, cm["corr"], vols)

    return {
        "status": "OK",
        "strategies": len(aligned),
        "unalignable": unalignable,
        "days": len(dates),
        "first_day": dates[0] if dates else None,
        "last_day": dates[-1] if dates else None,
        "portfolio_return_pct": round((equity - 1) * 100.0, 2),
        "portfolio_vol_annual_pct": round(sd * math.sqrt(252) * 100.0, 2),
        "portfolio_sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(dd, 2),
        "weights": {k: round(v / wsum, 4) for k, v in w.items()},
        "hhi": round(hhi, 3),
        "largest_weight": round(largest, 4),
        "risk_contribution_pct": rc,
        "correlation": cm["corr"],
        "correlation_overlap_days": cm["overlap"],
        "gate_passed": bool(dd <= MAX_PORTFOLIO_DD_PCT and largest <= MAX_SINGLE_STRATEGY_WEIGHT + 1e-9),
    }


def gate(risk: Dict[str, Any]) -> Dict[str, Any]:
    """Pass/fail with the reasons that failed."""
    reasons: List[str] = []
    if risk.get("status") != "OK":
        return {"passed": False, "reasons": ["no calendar-aligned strategy returns"]}
    if risk["max_drawdown_pct"] > MAX_PORTFOLIO_DD_PCT:
        reasons.append(f"book drawdown {risk['max_drawdown_pct']:.1f}% > {MAX_PORTFOLIO_DD_PCT:.0f}%")
    if risk["largest_weight"] > MAX_SINGLE_STRATEGY_WEIGHT + 1e-9:
        reasons.append(
            f"largest weight {risk['largest_weight']:.0%} > {MAX_SINGLE_STRATEGY_WEIGHT:.0%}"
        )
    if risk["hhi"] > 0.35:
        reasons.append(f"concentration HHI {risk['hhi']:.2f} > 0.35")
    return {"passed": not reasons, "reasons": reasons}


def from_registry(only_validated: bool = True, limit: int = 20) -> Dict[str, Any]:
    """Recompute the book from the strategy registry (cached frames, offline)."""
    from engine.v2 import strategy_lab

    entries = (strategy_lab.load_registry().get("strategies") or [])
    if only_validated:
        entries = [e for e in entries if e.get("validated")]
    runs: Dict[str, Any] = {}
    frames: Dict[str, Any] = {}
    skipped: List[str] = []
    for entry in entries[:limit]:
        try:
            spec = strategy_lab.StrategySpec(
                family=str(entry.get("family") or "BREAKOUT"),
                index_key=str(entry.get("index") or "NIFTY"),
                params=dict(entry.get("params") or {}),
                name=str(entry.get("name") or ""),
            )
        except Exception:
            skipped.append(str(entry.get("name")))
            continue
        key = spec.index_key
        if key not in frames:
            frames[key] = strategy_lab._frame(key)
        if frames[key] is None or not len(frames[key]):
            skipped.append(spec.name)
            continue
        runs[entry.get("name") or spec.name] = strategy_lab.backtest(frames[key], spec)
    risk = book_risk(runs)
    return {"risk": risk, "gate": gate(risk), "skipped": skipped}


if __name__ == "__main__":
    import json

    print(json.dumps(from_registry(), indent=2, default=str)[:6000])
