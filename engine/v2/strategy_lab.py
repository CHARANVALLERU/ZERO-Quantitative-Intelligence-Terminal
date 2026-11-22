"""
ZERO Engine v2.0 — Strategy Lab
===============================

Turns market history into **named, documented, backtested strategies** that a
human can read, audit and reuse.

What changed in the 2.1 rework (and why)
----------------------------------------
The first lab shipped six pattern families and 43 presets. A measured audit of
those presets showed the patterns had no edge on daily index bars: profit
factors clustered at 0.5-1.1, and **every** short-side preset lost money
(short PF 0.2-0.6 across the board) because these indices drift upward. The
only mechanism with a repeatable, out-of-sample edge was **time-series
momentum**: buying a new N-day high while price is above a rising regime mean,
with a wide ATR stop and a trailing exit that keeps the right tail.

So the lab now trades one proven mechanism through four genuinely different
horizons/filters, long-only by default, and it reports validation instead of
searching 40 parameter mutations until something passes:

* ``BREAKOUT`` — swing breakout (15-30 day high) in a rising regime.
* ``MAJOR``    — position breakout (40-55 day high), fewer, larger trades.
* ``PULLBACK`` — breakout that is *not extended* from the short mean, i.e. the
  first thrust out of a pullback rather than a chased move.
* ``TREND``    — regime cross with a hold-until-regime-loss exit.

Contract (unchanged):

* Every strategy gets an alphanumeric id: ``Z2-<FAMILY>-<INDEX2><HASH4>``.
* Every strategy is written to ``strategies/<ID>.md`` in plain language.
* ``strategies/_@<ID>, mistakes and P&L.md`` carries the run's measured
  mistakes and the worst/best trades.
* ``strategies/index.json`` is the registry the UI and the daily brief read.
* The gate is untouched: >=20 trades, profit factor >=1.15, drawdown <=12%,
  and positive net P&L. Validation (holdout + fold consistency) is reported
  *in addition* to the gate, never instead of it.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SCHEMA_VERSION = "2.1"
STRATEGY_DIR = "strategies"
REGISTRY_PATH = os.path.join(STRATEGY_DIR, "index.json")
CACHE_DIR = os.path.join("db", "v2", "cache")

DEFAULT_CAPITAL = 1_000_000.0
DEFAULT_RISK_PCT = 0.75          # % of capital risked per trade
DEFAULT_COST_BPS = 12.0          # per leg: brokerage + STT + slippage proxy
MIN_TRADES_TO_PASS = 20
MIN_PROFIT_FACTOR = 1.15
MAX_DRAWDOWN_PCT = 12.0
HOLDOUT_FRACTION = 0.30
MIN_HOLDOUT_TRADES = 5


# ═══════════════════════════════════════════════════════════════════════════
#  Data (cached, with provenance)
# ═══════════════════════════════════════════════════════════════════════════

def _cache_paths(index_key: str) -> Tuple[str, str]:
    stem = os.path.join(CACHE_DIR, f"{index_key.upper()}")
    return stem + ".pkl", stem + ".json"


def _read_cache(index_key: str):
    """Return cached OHLCV when it is younger than a day, else None."""
    import pandas as pd

    pkl, meta = _cache_paths(index_key)
    try:
        if os.path.getmtime(pkl) > _dt.datetime.now().timestamp() - 86_400:
            df = pd.read_pickle(pkl)
            if df is not None and len(df) > 200:
                return df
    except (OSError, ValueError):
        pass
    return None


def _write_cache(index_key: str, df) -> None:
    pkl, meta = _cache_paths(index_key)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        df.to_pickle(pkl)
        with open(meta, "w", encoding="utf-8") as fh:
            json.dump({
                "index": index_key,
                "source": "yfinance:get_historical_data(10y)",
                "rows": int(len(df)),
                "first": str(df.index[0])[:10],
                "last": str(df.index[-1])[:10],
                "fetched_at": _dt.datetime.now().isoformat(timespec="seconds"),
            }, fh, indent=2)
    except Exception:
        pass


def _frame(index_key: str, period: str = "10y", refresh: bool = False):
    """Load daily OHLCV for an index key. Cached for a day; None on failure."""
    if not refresh:
        cached = _read_cache(index_key)
        if cached is not None:
            return cached
    try:
        from data.historical import get_historical_data
        from engine.v2.aggregator import _flatten_ohlcv
        df = _flatten_ohlcv(get_historical_data(index_key, period=period))
        if df is not None and len(df):
            _write_cache(index_key, df)
        return df
    except Exception:
        return None


def _atr_series(df, period: int = 14):
    import pandas as pd
    high, low, close = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([(high - low), (high - close).abs(), (low - close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ═══════════════════════════════════════════════════════════════════════════
#  Strategy specification
# ═══════════════════════════════════════════════════════════════════════════

DEFAULT_PARAMS: Dict[str, Any] = {
    "regime": 200,          # structural mean used as the trade permission gate
    "slope_len": 20,        # the mean must actually be rising, not merely flat
    "confirm": 0.55,        # close must finish in the trade's half of the bar
    "exit_regime": 1,       # flatten when price closes back through the mean
    "trail_atr": 6.0,       # chandelier trail: the mechanism that keeps the tail
    "stop_atr": 2.0,        # initial ATR stop distance
    "time_stop": 400,       # effectively a safety valve, not a strategy exit
    "direction_mode": "long",  # shorts measured negative; opt in only deliberately
}


@dataclass
class StrategySpec:
    """A fully described, reproducible rule set."""

    family: str                     # BREAKOUT, MAJOR, PULLBACK, TREND
    index_key: str                  # NIFTY | BANKNIFTY | SENSEX
    params: Dict[str, float] = field(default_factory=dict)
    name: str = ""
    thesis: str = ""
    entry_text: str = ""
    exit_text: str = ""
    risk_text: str = ""
    source_study: str = ""

    def __post_init__(self) -> None:
        # Structural guard shared by every spec. Folded in before the name hash
        # so two specs with different guards get different ids.
        for key, value in DEFAULT_PARAMS.items():
            self.params.setdefault(key, value)
        if not self.name:
            self.name = make_name(self.family, self.index_key, self.params)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def make_name(family: str, index_key: str, params: Dict[str, float]) -> str:
    """Deterministic alphanumeric id: Z2-<FAM>-<INDEX2><HASH4>."""
    payload = json.dumps({"f": family, "i": index_key, "p": params}, sort_keys=True, default=str)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest().upper()
    idx = "".join(ch for ch in index_key.upper() if ch.isalnum())[:2]
    return f"Z2-{family}-{idx}{digest[:4]}"


# ═══════════════════════════════════════════════════════════════════════════
#  Signals
# ═══════════════════════════════════════════════════════════════════════════

def _signals(df, spec: StrategySpec) -> Dict[str, Any]:
    """Entry/exit masks. Every decision uses closed bars only.

    The regime gate is the single most important filter: longs need price above
    a mean that has risen over ``slope_len`` bars, shorts need the mirror. A
    flat or falling mean is not a trend, and every measured attempt to trade
    against it lost money.
    """
    import pandas as pd

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    atr = _atr_series(df)
    p = spec.params
    false = pd.Series(False, index=df.index)

    regime_len = int(p.get("regime", 0) or 0)
    regime_sma = close.rolling(regime_len).mean() if regime_len > 1 else false.astype(float)
    slope_len = int(p.get("slope_len", 20) or 20)
    slope = regime_sma.diff(slope_len) if regime_len > 1 else false.astype(float)
    up_regime = (close > regime_sma) & (slope > 0) if regime_len > 1 else pd.Series(True, index=df.index)
    down_regime = (close < regime_sma) & (slope < 0) if regime_len > 1 else up_regime.copy()

    bar_range = (high - low).replace(0, float("nan"))
    close_pos = (close - low) / bar_range
    confirm = float(p.get("confirm", 0.0) or 0.0)
    confirm_long = close_pos >= confirm if confirm > 0 else pd.Series(True, index=df.index)
    confirm_short = (1.0 - close_pos) >= confirm if confirm > 0 else confirm_long.copy()

    long_entry = false.copy()
    short_entry = false.copy()
    long_exit = false.copy()
    short_exit = false.copy()

    if spec.family in ("BREAKOUT", "MAJOR"):
        lookback = int(p.get("lookback", 20))
        prior_high = high.rolling(lookback).max().shift(1)
        prior_low = low.rolling(lookback).min().shift(1)
        long_entry = close > prior_high
        short_entry = close < prior_low

    elif spec.family == "PULLBACK":
        lookback = int(p.get("lookback", 10))
        fast = close.rolling(int(p.get("fast", 20))).mean()
        prior_high = high.rolling(lookback).max().shift(1)
        # A breakout is only a *pullback* trade while price is still near the
        # short mean; once it is extended the same entry is chasing.
        long_entry = (close > prior_high) & (close <= fast * (1 + float(p.get("ext", 0.02))))

    elif spec.family == "TREND":
        long_entry = (close > regime_sma) & (close.shift(1) <= regime_sma.shift(1))
        short_entry = (close < regime_sma) & (close.shift(1) >= regime_sma.shift(1))
        long_exit = close < regime_sma
        short_exit = close > regime_sma

    long_entry = long_entry & up_regime & confirm_long
    short_entry = short_entry & down_regime & confirm_short

    if str(p.get("direction_mode", "long")) != "long_short":
        short_entry = false.copy()

    if regime_len > 1 and int(p.get("exit_regime", 1) or 0):
        long_exit = long_exit | (close < regime_sma)
        short_exit = short_exit | (close > regime_sma)

    return {
        "long_entry": long_entry.fillna(False),
        "short_entry": short_entry.fillna(False),
        "long_exit": long_exit.fillna(False),
        "short_exit": short_exit.fillna(False),
        "atr": atr,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Backtest
# ═══════════════════════════════════════════════════════════════════════════

def _warmup(spec: StrategySpec) -> int:
    return max(
        int(spec.params.get("regime", 0) or 0),
        int(spec.params.get("fast", 0) or 0),
        int(spec.params.get("lookback", 0) or 0),
    ) + 5


def _trade_row(pos, side, exit_price, exit_date, reason, bars_held, net) -> Dict[str, Any]:
    risk0 = float(pos["risk0"])
    return {
        "side": "LONG" if side > 0 else "SHORT",
        "entry_date": pos["entry_date"],
        "exit_date": exit_date,
        "entry": round(pos["entry"], 2),
        "exit": round(exit_price, 2),
        "qty": round(pos["qty"], 3),
        "stop": round(pos["initial_stop"], 2),
        "pnl": round(net, 2),
        "r_multiple": round(((exit_price - pos["entry"]) * side) / max(1e-9, risk0), 2),
        "reason": reason,
        "bars_held": bars_held,
        "mae_pts": round(pos["mae"], 1),
        "mfe_pts": round(pos["mfe"], 1),
        "stop_atr": round(risk0, 1),
        "entry_atr": round(pos["entry_atr"], 1),
    }


def backtest(
    df,
    spec: StrategySpec,
    capital: float = DEFAULT_CAPITAL,
    risk_pct: float = DEFAULT_RISK_PCT,
    cost_bps: float = DEFAULT_COST_BPS,
    start: Optional[int] = None,
    end: Optional[int] = None,
) -> Dict[str, Any]:
    """Regime-gated, ATR-stopped, trailing, cost-charged backtest.

    Entry fills at the **next bar's open** after a signal on the close, so there
    is no look-ahead. The stop is checked before the target, and the watcher
    stop only ratchets from bar to bar (never from the same bar's high), so a
    single bar cannot both raise the stop and trigger it.
    """
    warmup = _warmup(spec)
    start = max(int(start if start is not None else warmup), warmup)
    if df is None or len(df) < start + 20:
        return {"status": "NO_DATA", "trades": [], "metrics": {}}

    d = df.reset_index(drop=False)
    if "date" not in d.columns:
        d = d.rename(columns={d.columns[0]: "date"})

    signals = _signals(d.set_index("date"), spec)
    entry_long = signals["long_entry"].reset_index(drop=True)
    entry_short = signals["short_entry"].reset_index(drop=True)
    exit_long = signals["long_exit"].reset_index(drop=True)
    exit_short = signals["short_exit"].reset_index(drop=True)
    atr = signals["atr"].reset_index(drop=True)

    open_ = d["Open"].astype(float).reset_index(drop=True)
    high = d["High"].astype(float).reset_index(drop=True)
    low = d["Low"].astype(float).reset_index(drop=True)
    close = d["Close"].astype(float).reset_index(drop=True)
    dates = [str(x)[:10] for x in d["date"]]

    end = len(d) if end is None else min(int(end), len(d))
    stop_atr = float(spec.params.get("stop_atr", 2.0))
    trail_atr = float(spec.params.get("trail_atr", 0.0) or 0.0)
    time_stop = int(spec.params.get("time_stop", 400))

    trades: List[Dict[str, Any]] = []
    eq = capital
    peak = capital
    max_dd = 0.0
    curve: List[Tuple[str, float]] = [(dates[start], round(eq, 2))]
    pos: Optional[Dict[str, Any]] = None

    for i in range(start, end - 1):
        if pos is None:
            side = 1 if bool(entry_long.iloc[i]) else (-1 if bool(entry_short.iloc[i]) else 0)
            if side == 0:
                continue
            a = float(atr.iloc[i])
            if not math.isfinite(a) or a <= 0:
                continue
            entry_price = float(open_.iloc[i + 1])
            risk = stop_atr * a
            if risk <= 0:
                continue
            qty = (eq * risk_pct / 100.0) / risk
            pos = {
                "side": side,
                "entry_date": dates[i + 1],
                "entry": entry_price,
                "stop": entry_price - side * risk,
                "initial_stop": entry_price - side * risk,
                "qty": qty,
                "entry_i": i + 1,
                "cost": entry_price * qty * cost_bps / 10_000.0,
                "mae": 0.0,
                "mfe": 0.0,
                "best": entry_price,
                "entry_atr": a,
                "risk0": risk,
            }
            continue

        j = i
        if j < pos["entry_i"]:
            continue
        side = int(pos["side"])

        if side > 0:
            pos["best"] = max(pos["best"], float(high.iloc[j]))
            pos["mfe"] = max(pos["mfe"], float(high.iloc[j]) - pos["entry"])
            pos["mae"] = max(pos["mae"], pos["entry"] - float(low.iloc[j]))
        else:
            pos["best"] = min(pos["best"], float(low.iloc[j]))
            pos["mfe"] = max(pos["mfe"], pos["entry"] - float(low.iloc[j]))
            pos["mae"] = max(pos["mae"], float(high.iloc[j]) - pos["entry"])

        exit_price = None
        reason = ""
        if side > 0:
            if float(low.iloc[j]) <= pos["stop"]:
                exit_price, reason = pos["stop"], "STOP"
            elif bool(exit_long.iloc[j]):
                exit_price, reason = float(close.iloc[j]), "SIGNAL_EXIT"
        else:
            if float(high.iloc[j]) >= pos["stop"]:
                exit_price, reason = pos["stop"], "STOP"
            elif bool(exit_short.iloc[j]):
                exit_price, reason = float(close.iloc[j]), "SIGNAL_EXIT"
        if exit_price is None and j - pos["entry_i"] >= time_stop:
            exit_price, reason = float(close.iloc[j]), "TIME_STOP"

        if exit_price is None:
            if trail_atr > 0 and math.isfinite(float(atr.iloc[j])):
                trail = pos["best"] - side * trail_atr * float(atr.iloc[j])
                pos["stop"] = max(pos["stop"], trail) if side > 0 else min(pos["stop"], trail)
            continue

        gross = (exit_price - pos["entry"]) * pos["qty"] * side
        exit_cost = exit_price * pos["qty"] * cost_bps / 10_000.0
        net = gross - pos["cost"] - exit_cost
        eq += net
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100.0 if peak else 0.0)

        trades.append(_trade_row(pos, side, exit_price, dates[j], reason, j - pos["entry_i"], net))
        curve.append((dates[j], round(eq, 2)))
        pos = None

    if pos is not None:
        side = int(pos["side"])
        exit_price = float(close.iloc[end - 1])
        net = (exit_price - pos["entry"]) * pos["qty"] * side - pos["cost"]
        eq += net
        trades.append(_trade_row(
            pos, side, exit_price, dates[end - 1], "MARK_TO_MARKET",
            end - 1 - pos["entry_i"], net,
        ))
        curve.append((dates[end - 1], round(eq, 2)))

    return {
        "status": "OK" if trades else "NO_TRADES",
        "spec": spec.to_dict(),
        "trades": trades,
        "metrics": _metrics(trades, capital, eq, max_dd, curve, dates, start, end),
    }


def _metrics(
    trades: List[Dict[str, Any]],
    capital: float,
    equity: float,
    max_dd: float,
    curve: List[Tuple[str, float]],
    dates: List[str],
    start: int,
    end: int,
) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "trades": 0, "net_pnl": 0.0, "return_pct": 0.0, "win_rate_pct": 0.0,
            "profit_factor": 0.0, "expectancy_r": 0.0, "max_drawdown_pct": 0.0,
            "sharpe": 0.0, "cagr_pct": 0.0, "passed": False,
        }
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
    returns = [t["pnl"] / capital for t in trades]
    mean_r = sum(returns) / n
    std_r = (sum((r - mean_r) ** 2 for r in returns) / max(1, n - 1)) ** 0.5
    sharpe = (mean_r / std_r * math.sqrt(max(1, n))) if std_r > 0 else 0.0
    years = max(1e-9, (end - start) / 252.0)
    cagr = ((equity / capital) ** (1 / years) - 1) * 100.0 if equity > 0 else -100.0
    passed = (
        n >= MIN_TRADES_TO_PASS
        and pf >= MIN_PROFIT_FACTOR
        and max_dd <= MAX_DRAWDOWN_PCT
        and equity > capital
    )
    return {
        "trades": n,
        "net_pnl": round(equity - capital, 2),
        "return_pct": round((equity / capital - 1) * 100.0, 2),
        "win_rate_pct": round(len(wins) / n * 100.0, 1),
        "profit_factor": round(pf, 2),
        "expectancy_r": round(sum(t["r_multiple"] for t in trades) / n, 3),
        "avg_r": round(sum(t["r_multiple"] for t in trades) / n, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": round(sharpe, 2),
        "cagr_pct": round(cagr, 2),
        "avg_bars_held": round(sum(t["bars_held"] for t in trades) / n, 1),
        "stop_rate_pct": round(sum(1 for t in trades if t["reason"] == "STOP") / n * 100.0, 1),
        "long_trades": sum(1 for t in trades if t.get("side") == "LONG"),
        "short_trades": sum(1 for t in trades if t.get("side") == "SHORT"),
        "passed": bool(passed),
    }


def _oos_split(df) -> Tuple[int, int]:
    n = len(df)
    return int(n * (1 - HOLDOUT_FRACTION)), n


def walk_forward(df, spec: StrategySpec, folds: int = 5) -> Dict[str, Any]:
    """Split the post-warmup history into contiguous folds. Rules are fixed, so
    this measures *consistency through time*, not fitting: how many regimes the
    edge survives, and the pooled profit factor across all folds."""
    warmup = _warmup(spec)
    span = len(df) - warmup
    if span < folds * 60:
        return {"folds": 0, "positive_folds": 0, "pooled_pf": 0.0, "trades": 0}
    edges = [warmup + round(span * k / folds) for k in range(folds + 1)]
    wins = losses = trades = positive = 0.0
    for k in range(folds):
        r = backtest(df, spec, start=edges[k], end=edges[k + 1])
        pnls = [t["pnl"] for t in r.get("trades", [])]
        if not pnls:
            continue
        w = sum(x for x in pnls if x > 0)
        l = abs(sum(x for x in pnls if x <= 0))
        wins += w
        losses += l
        trades += len(pnls)
        positive += 1 if sum(pnls) > 0 else 0
    pooled = wins / losses if losses else (99.0 if wins else 0.0)
    return {
        "folds": folds,
        "positive_folds": int(positive),
        "pooled_pf": round(pooled, 2),
        "trades": int(trades),
    }


def validate(df, spec: StrategySpec) -> Dict[str, Any]:
    """Holdout + fold consistency, reported independently of the gate."""
    split, n = _oos_split(df)
    hold = backtest(df, spec, start=split, end=n).get("metrics", {})
    wf = walk_forward(df, spec)
    validated = (
        hold.get("trades", 0) >= MIN_HOLDOUT_TRADES
        and hold.get("profit_factor", 0.0) >= 1.0
        and hold.get("net_pnl", 0.0) > 0
        and wf.get("positive_folds", 0) >= math.ceil(wf.get("folds", 5) * 0.6)
    )
    return {
        "holdout": hold,
        "walk_forward": wf,
        "validated": bool(validated),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Families
# ═══════════════════════════════════════════════════════════════════════════

FAMILIES: Dict[str, Dict[str, Any]] = {
    "BREAKOUT": {
        "thesis": (
            "Time-series momentum: an index that prints a new 15-30 day high while above a "
            "rising 200-day mean is being repriced, not exhausted. Trend persistence is one of "
            "the most replicated effects in index data, and it survives costs because the "
            "average winner is several times the ATR stop."
        ),
        "entry": "Buy the next open when the close exceeds the prior {lookback}-day high.",
        "exit": "Trail a {trail_atr}× ATR chandelier stop under the highest high since entry; "
                "flatten on a close back below the regime mean or after {time_stop} bars.",
        "grid": [
            {"lookback": 15, "stop_atr": 2.0, "trail_atr": 6.0, "time_stop": 400},
            {"lookback": 20, "stop_atr": 2.0, "trail_atr": 6.0, "time_stop": 400},
            {"lookback": 30, "stop_atr": 2.0, "trail_atr": 6.0, "time_stop": 400},
        ],
    },
    "MAJOR": {
        "thesis": (
            "The same momentum effect on a position horizon. A 40-55 day high is rarer, so the "
            "sample is smaller, but each signal marks a slower, better-established repricing "
            "and the trailing exit can hold it for months."
        ),
        "entry": "Buy the next open when the close exceeds the prior {lookback}-day high.",
        "exit": "Trail a {trail_atr}× ATR chandelier stop; flatten on a close back below the "
                "regime mean or after {time_stop} bars.",
        "grid": [
            {"lookback": 40, "stop_atr": 2.0, "trail_atr": 6.0, "time_stop": 400},
            {"lookback": 55, "stop_atr": 3.0, "trail_atr": 6.0, "time_stop": 400},
        ],
    },
    "PULLBACK": {
        "thesis": (
            "A breakout is only tradeable while price is still near the short mean. Once the move "
            "is extended, the same breakout is the crowd's entry. Requiring the close to sit "
            "within {ext} of the {fast}-day mean keeps the entry on the first thrust out of a "
            "pullback and skips the chase."
        ),
        "entry": "Buy the next open when the close exceeds the prior {lookback}-day high and is "
                 "no more than {ext} above the {fast}-day mean.",
        "exit": "Trail a {trail_atr}× ATR chandelier stop; flatten on a close back below the "
                "regime mean or after {time_stop} bars.",
        "grid": [
            {"lookback": 10, "fast": 20, "ext": 0.02, "stop_atr": 2.0, "trail_atr": 6.0, "time_stop": 400},
            {"lookback": 10, "fast": 50, "ext": 0.01, "stop_atr": 2.0, "trail_atr": 6.0, "time_stop": 400},
            {"lookback": 10, "fast": 20, "ext": 0.01, "stop_atr": 2.0, "trail_atr": 6.0, "time_stop": 400},
        ],
    },
    "TREND": {
        "thesis": (
            "The slowest expression of the same edge: be long while price holds above a rising "
            "200-day mean, flat otherwise. Few trades, long holds, and it is the honest baseline "
            "any faster strategy has to beat."
        ),
        "entry": "Buy the next open when the close crosses above the {regime}-day mean while that "
                 "mean is rising.",
        "exit": "Exit on a close back below the {regime}-day mean, the {stop_atr}× ATR stop, or "
                "after {time_stop} bars.",
        "grid": [
            {"stop_atr": 3.0, "trail_atr": 0.0, "time_stop": 400},
            {"stop_atr": 2.0, "trail_atr": 6.0, "time_stop": 400},
        ],
    },
}


def _describe(spec: StrategySpec) -> StrategySpec:
    fam = FAMILIES.get(spec.family, {})
    params = {k: _fmt(v) for k, v in spec.params.items()}
    gate = (
        f" The regime gate requires price above a rising {int(spec.params.get('regime', 200))}-day "
        f"mean (shorts: below a falling one), measured over {int(spec.params.get('slope_len', 20))} bars, "
        "and the signal bar must close in the trade's half of its range."
    )
    spec.thesis = fam.get("thesis", spec.thesis).format(**params) + gate
    spec.entry_text = fam.get("entry", spec.entry_text).format(**params) + gate
    spec.exit_text = fam.get("exit", spec.exit_text).format(**params)

    trail = float(spec.params.get("trail_atr", 0) or 0)
    short_side = str(spec.params.get("direction_mode", "long")) == "long_short"
    spec.risk_text = (
        ("Long and short (shorts only in a confirmed downtrend). " if short_side
         else "Long only: measured shorts lost money on these indices. ")
        + f"Initial stop {spec.params.get('stop_atr', 2.0)}× ATR, "
        + (f"trailed {trail:g}× ATR once the trade is in profit; " if trail > 0 else "not trailed; ")
        + f"time stop {spec.params.get('time_stop', 400)} bars; "
        + f"{DEFAULT_RISK_PCT}% of equity risked per trade; {DEFAULT_COST_BPS:.0f} bps charged per leg."
    )
    return spec


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ═══════════════════════════════════════════════════════════════════════════
#  Docs
# ═══════════════════════════════════════════════════════════════════════════

def _ensure_dir() -> str:
    os.makedirs(STRATEGY_DIR, exist_ok=True)
    return STRATEGY_DIR


def render_strategy_doc(
    spec: StrategySpec,
    result: Dict[str, Any],
    validation: Optional[Dict[str, Any]] = None,
) -> str:
    m = result.get("metrics", {})
    v = validation or {}
    hold = v.get("holdout", {})
    wf = v.get("walk_forward", {})
    status = "PASSED" if m.get("passed") else "NOT PASSED"
    lines = [
        f"# {spec.name} — {spec.family} on {spec.index_key}",
        "",
        f"**Gate:** {status}  ",
        f"**Validation:** {'VALIDATED' if v.get('validated') else 'NOT VALIDATED'}  ",
        f"**Generated:** {_dt.datetime.now().isoformat(timespec='seconds')}  ",
        f"**Engine:** ZERO v{SCHEMA_VERSION} Strategy Lab",
        "",
        "## Why this edge should exist",
        "",
        spec.thesis or "—",
        "",
        "## Rules (plain language)",
        "",
        f"- **Entry:** {spec.entry_text}",
        f"- **Exit:** {spec.exit_text}",
        f"- **Risk:** {spec.risk_text}",
        "",
        "## Parameters",
        "",
        "| Parameter | Value |",
        "|---|---|",
    ]
    for key, value in sorted(spec.params.items()):
        lines.append(f"| `{key}` | {value} |")
    lines += [
        "",
        "## Backtest result (full history)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Trades | {m.get('trades', 0)} |",
        f"| Net P&L (₹) | {m.get('net_pnl', 0):,.0f} |",
        f"| Return | {m.get('return_pct', 0):+.2f}% |",
        f"| CAGR | {m.get('cagr_pct', 0):+.2f}% |",
        f"| Win rate | {m.get('win_rate_pct', 0):.1f}% |",
        f"| Profit factor | {m.get('profit_factor', 0):.2f} |",
        f"| Expectancy (R) | {m.get('expectancy_r', 0):+.3f} |",
        f"| Max drawdown | {m.get('max_drawdown_pct', 0):.2f}% |",
        f"| Stop-out rate | {m.get('stop_rate_pct', 0):.1f}% |",
        f"| Long / short trades | {m.get('long_trades', 0)} / {m.get('short_trades', 0)} |",
        "",
        "## Validation (never part of the gate)",
        "",
        "| Check | Value |",
        "|---|---|",
        f"| Holdout trades | {hold.get('trades', 0)} |",
        f"| Holdout profit factor | {hold.get('profit_factor', 0):.2f} |",
        f"| Holdout net P&L (₹) | {hold.get('net_pnl', 0):,.0f} |",
        f"| Fold-consistency | {wf.get('positive_folds', 0)}/{wf.get('folds', 0)} folds positive |",
        f"| Pooled fold profit factor | {wf.get('pooled_pf', 0):.2f} |",
        "",
        "## How to reproduce",
        "",
        "```python",
        "from engine.v2.strategy_lab import StrategySpec, backtest, validate, _frame",
        f"spec = StrategySpec(family='{spec.family}', index_key='{spec.index_key}', params={spec.params!r})",
        f"df = _frame('{spec.index_key}')",
        "print(backtest(df, spec)['metrics'])",
        "print(validate(df, spec))",
        "```",
        "",
        "## Reuse",
        "",
        f"Load the spec from `strategies/index.json` under `{spec.name}` and pass it to",
        "`backtest()` or the v2.0 panel. The id is stable for the same parameter set.",
        "",
        "---",
        "_Backtests are simulations. Costs are approximated at "
        f"{DEFAULT_COST_BPS:.0f} bps per leg and do not model gap slippage beyond the "
        "next-open fill. Past performance is not a forecast._",
    ]
    return "\n".join(lines)


def render_mistakes_doc(
    spec: StrategySpec,
    result: Dict[str, Any],
    validation: Optional[Dict[str, Any]] = None,
) -> str:
    m = result.get("metrics", {})
    trades = result.get("trades", []) or []
    stops = [t for t in trades if t["reason"] == "STOP"]
    losers = sorted(trades, key=lambda t: t["pnl"])[:3]
    winners = sorted(trades, key=lambda t: t["pnl"], reverse=True)[:3]
    premature = [t for t in stops if t["mfe_pts"] > 1.5 * max(1e-9, t["stop_atr"])]
    giveback = [t for t in trades if t["mfe_pts"] > 0 and t["pnl"] < 0.25 * t["mfe_pts"] * t["qty"]]
    v = validation or {}
    hold = v.get("holdout", {})

    lines = [
        f"# _@{spec.name}_, mistakes and P&L",
        "",
        f"**Index:** {spec.index_key}  ",
        f"**Family:** {spec.family}  ",
        f"**Trades:** {m.get('trades', 0)}  ",
        f"**Net P&L:** ₹{m.get('net_pnl', 0):,.0f} ({m.get('return_pct', 0):+.2f}%)  ",
        f"**Profit factor:** {m.get('profit_factor', 0):.2f}  ",
        f"**Max drawdown:** {m.get('max_drawdown_pct', 0):.2f}%",
        "",
        "## What actually went wrong",
        "",
    ]
    if not trades:
        lines.append("- No trades were produced. The rule never fired in this window; that is a")
        lines.append("  design failure (too strict a filter), not a market failure.")
    else:
        if premature:
            lines.append(
                f"- **Premature stop-outs.** {len(premature)} stop-outs had already run more than "
                "1.5× their stop distance in favour before reversing."
            )
        if giveback:
            lines.append(
                f"- **Give-back.** {len(giveback)} trades closed below a quarter of their peak "
                "favour. The trail is wide by design; this is the cost of letting the tail run."
            )
        if m.get("stop_rate_pct", 0) > 60:
            lines.append(f"- **Stop rate {m.get('stop_rate_pct', 0):.0f}%.** The entry filter "
                         "admits too many low-quality setups.")
        if m.get("profit_factor", 0) < MIN_PROFIT_FACTOR:
            lines.append(f"- **Profit factor {m.get('profit_factor', 0):.2f}.** Gross wins do not "
                         f"cover gross losses after costs. The edge is absent or too small.")
        if hold.get("trades") and hold.get("net_pnl", 0) <= 0:
            lines.append(f"- **Holdout lost money:** ₹{hold.get('net_pnl', 0):,.0f} over "
                         f"{hold.get('trades', 0)} trades. The result is history-specific.")
        if not premature and not giveback and m.get("profit_factor", 0) >= MIN_PROFIT_FACTOR:
            lines.append("- No structural mistake cleared its threshold. The losses are variance "
                         "around a positive expectancy rather than a repeated error.")

    lines += ["", "## Trade ledger (worst 3 / best 3)", ""]
    lines.append("| Entry | Exit | Entry px | Exit px | P&L ₹ | R | Reason | MAE | MFE |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for t in losers + winners:
        lines.append(
            f"| {t['entry_date']} | {t['exit_date']} | {t['entry']:,.2f} | {t['exit']:,.2f} | "
            f"{t['pnl']:,.0f} | {t['r_multiple']:+.2f} | {t['reason']} | {t['mae_pts']:,.0f} | {t['mfe_pts']:,.0f} |"
        )
    lines += [
        "",
        "## P&L attribution",
        "",
        f"- Winners: {len([t for t in trades if t['pnl'] > 0])} · losers: {len([t for t in trades if t['pnl'] <= 0])}.",
        f"- Average hold: {m.get('avg_bars_held', 0)} bars.",
        f"- Expectancy: {m.get('expectancy_r', 0):+.3f}R per trade.",
        "",
        "---",
        "_Generated by ZERO v2.1 Strategy Lab. Metrics are computed from the trade ledger above._",
    ]
    return "\n".join(lines)


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ═══════════════════════════════════════════════════════════════════════════
#  Registry
# ═══════════════════════════════════════════════════════════════════════════

def load_registry() -> Dict[str, Any]:
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _rank_score(metrics: Dict[str, Any]) -> float:
    """Risk-adjusted rank used for the podium."""
    return metrics.get("profit_factor", 0) * min(1.0, metrics.get("trades", 0) / 20.0)


def _save_registry(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    passed = [e for e in entries if e.get("status") == "PASSED"]
    validated = [e for e in entries if e.get("validated")]
    podium = sorted(passed or entries, key=lambda e: _rank_score(e.get("metrics", {})), reverse=True)[:3]
    registry = {
        "schema": SCHEMA_VERSION,
        "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "total": len(entries),
        "passed": len(passed),
        "failed": len(entries) - len(passed),
        "validated": len(validated),
        "top": [
            {
                "name": e["name"], "index": e.get("index"), "family": e.get("family"),
                "status": e.get("status"), "metrics": e.get("metrics", {}),
                "validated": e.get("validated", False),
            }
            for e in podium
        ],
        "strategies": entries,
    }
    _write(REGISTRY_PATH, json.dumps(registry, indent=2, default=str))
    return registry


def _prune_orphans(names: set) -> None:
    """Delete generated docs that no longer belong to a current spec, so the
    folder always matches the registry after a family/grid change."""
    try:
        for fname in os.listdir(STRATEGY_DIR):
            if not fname.endswith(".md"):
                continue
            keep = (fname.startswith("_@") and any(f"@{n}_," in fname for n in names)) or (
                not fname.startswith("_@") and fname[:-3] in names
            )
            if not keep:
                os.remove(os.path.join(STRATEGY_DIR, fname))
    except OSError:
        pass


def _persist_run(spec: StrategySpec, result: Dict[str, Any], validation: Dict[str, Any]) -> Dict[str, Any]:
    """Write a strategy's two documents and build its registry entry."""
    doc_path = os.path.join(STRATEGY_DIR, f"{spec.name}.md")
    mistakes_path = os.path.join(STRATEGY_DIR, f"_@{spec.name}_, mistakes and P&L.md")
    _write(doc_path, render_strategy_doc(spec, result, validation))
    _write(mistakes_path, render_mistakes_doc(spec, result, validation))

    m = result.get("metrics", {})
    return {
        "name": spec.name,
        "family": spec.family,
        "index": spec.index_key,
        "status": "PASSED" if m.get("passed") else "NOT_PASSED",
        "validated": bool(validation.get("validated")),
        "params": spec.params,
        "metrics": m,
        "holdout": validation.get("holdout", {}),
        "walk_forward": validation.get("walk_forward", {}),
        "doc": doc_path.replace("\\", "/"),
        "mistakes_doc": mistakes_path.replace("\\", "/"),
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
    }


def run_lab(
    indices: Tuple[str, ...] = ("NIFTY", "SENSEX"),
    families: Tuple[str, ...] = tuple(FAMILIES.keys()),
    refresh_data: bool = False,
) -> Dict[str, Any]:
    """Generate, backtest, validate and document every family preset.

    Returns the registry plus a run summary. Never raises: a missing index or a
    failed family is recorded as a skipped entry.
    """
    _ensure_dir()
    entries: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []

    for index_key in indices:
        df = _frame(index_key, refresh=refresh_data)
        if df is None or len(df) < 300:
            skipped.append({"index": index_key, "reason": "history unavailable or too short"})
            continue
        for family in families:
            for params in FAMILIES.get(family, {}).get("grid", []):
                spec = _describe(StrategySpec(family=family, index_key=index_key, params=dict(params)))
                try:
                    result = backtest(df, spec)
                    validation = validate(df, spec)
                except Exception as exc:
                    skipped.append({"index": index_key, "family": family, "reason": str(exc)[:120]})
                    continue
                entries.append(_persist_run(spec, result, validation))

    _prune_orphans({e["name"] for e in entries})
    registry = _save_registry(entries)
    return {
        "schema": SCHEMA_VERSION,
        "registry": registry,
        "skipped": skipped,
        "created": len(entries),
        "passed": registry["passed"],
        "validated": registry["validated"],
        "top": registry["top"],
    }


def open_folder() -> Dict[str, Any]:
    """Open the strategies folder in the OS file manager (best effort)."""
    path = os.path.abspath(STRATEGY_DIR)
    os.makedirs(path, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return {"opened": True, "path": path}
    except Exception as exc:
        return {"opened": False, "path": path, "error": str(exc)}
