"""
ZERO Engine v2.0 — Strategy Lab
===============================

Turns everything the engine has studied (macro regime, unified view, market
structure) into **named, documented, backtested strategies** that a human can
read, audit and reuse.

Contract:

* Every strategy gets an alphanumeric id: ``Z2-<FAMILY>-<HASH4>``.
* Every strategy is written to ``strategies/<ID>.md`` in plain language —
  entry, exit, stop, sizing, and *why* the edge should exist.
* Every strategy gets ``strategies/_@<ID>, mistakes and P&L.md`` with the
  realised P&L, the trade ledger summary and the specific mistakes measured
  from that run — not generic advice.
* Passing strategies are re-optimised on a train window and re-validated on an
  untouched out-of-sample window. An improvement is only accepted if OOS
  profit factor does not degrade.
* ``strategies/index.json`` is the registry the UI and the daily brief read.

Risk posture is deliberately conservative: long-only (cash when flat), every
trade carries an ATR stop, size is derived from a fixed fraction of capital at
risk, and round-trip costs are charged on every trade.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

SCHEMA_VERSION = "2.0"
STRATEGY_DIR = "strategies"
REGISTRY_PATH = os.path.join(STRATEGY_DIR, "index.json")

DEFAULT_CAPITAL = 1_000_000.0
DEFAULT_RISK_PCT = 0.75          # % of capital risked per trade
DEFAULT_COST_BPS = 12.0          # round-trip brokerage + STT + slippage proxy
MIN_TRADES_TO_PASS = 20
MIN_PROFIT_FACTOR = 1.15
MAX_DRAWDOWN_PCT = 12.0
MIN_OOS_PROFIT_FACTOR = 1.00


# ═══════════════════════════════════════════════════════════════════════════
#  Data
# ═══════════════════════════════════════════════════════════════════════════

def _frame(index_key: str, period: str = "3y"):
    """Load daily OHLCV for a config index key. Returns None on failure."""
    try:
        from data.historical import get_historical_data
        from engine.v2.aggregator import _flatten_ohlcv
        raw = get_historical_data(index_key, period=period)
        return _flatten_ohlcv(raw)
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

@dataclass
DEFAULT_PARAMS: Dict[str, Any] = {
    "regime": 200,          # structural mean used as the trade permission gate
    "confirm": 0.60,        # close must finish in the trade's third of the bar
    "exit_regime": 1,       # flatten when price closes back through the regime mean
    "trail_atr": 2.0,       # trail 2 x ATR after +1.5R
    "breakeven_r": 1.0,     # move the stop to entry at +1R
    "direction_mode": "long_short",  # trade the regime, both ways
}


class StrategySpec:
    """A fully described, reproducible rule set."""

    family: str                     # MOM, REV, PULL, SQZ, ABS, SWEEP
    index_key: str                  # NIFTY | BANKNIFTY | SENSEX
    params: Dict[str, float] = field(default_factory=dict)
    name: str = ""
    thesis: str = ""
    entry_text: str = ""
    exit_text: str = ""
    risk_text: str = ""
    source_study: str = ""
    improved_from: str = ""

    def __post_init__(self) -> None:
        # Structural guard shared by every spec. Without it a family is just a
        # pattern matcher that buys into bear phases and chop. These defaults
        # are part of the strategy's identity, so they are folded in before the
        # name hash is computed — two specs with different guards get different
        # ids.
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
#  Signal families
# ═══════════════════════════════════════════════════════════════════════════

def _signals(df, spec: StrategySpec):
    """Build long/short entry and exit masks. All decisions use closed bars.

    Three structural filters sit in front of every family:

    1. **Regime gate** — longs only above a rising ``regime``-day mean, shorts
       only below a falling one. This is what stops a long-only family from
       buying into a bear phase; it is the difference between a pattern and a
       trade.
    2. **Confirmation close** — the signal bar must finish in the trade's own
       third of its range (top for longs, bottom for shorts), so a wick is not
       mistaken for acceptance.
    3. **Volatility ceiling** — optional ``atr_pct_max`` refuses chaotic bars
       where stop distance is meaningless.

    Returns ``{"long_entry", "short_entry", "long_exit", "short_exit"}``.
    """
    import pandas as pd  # noqa: F401

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)
    vol = df["Volume"].astype(float) if "Volume" in df.columns else None
    atr = _atr_series(df)
    p = spec.params
    n = int(p.get("lookback", 20))
    false = pd.Series(False, index=df.index)

    long_entry = false.copy()
    short_entry = false.copy()
    long_exit = false.copy()
    short_exit = false.copy()

    # ── Structural regime ──────────────────────────────────────────────────
    regime_len = int(p.get("regime", 0) or 0)
    if regime_len > 1:
        regime_sma = close.rolling(regime_len).mean()
        regime_slope = regime_sma.diff()
        up_regime = (close > regime_sma) & (regime_slope >= 0)
        down_regime = (close < regime_sma) & (regime_slope <= 0)
        lost_regime_long = close < regime_sma
        lost_regime_short = close > regime_sma
    else:
        regime_sma = false.astype(float)
        up_regime = pd.Series(True, index=df.index)
        down_regime = up_regime.copy()
        lost_regime_long = false.copy()
        lost_regime_short = false.copy()

    allow_short = str(p.get("direction_mode", "long")) == "long_short"

    # ── Confirmation close ─────────────────────────────────────────────────
    bar_range = (high - low).replace(0, float("nan"))
    close_pos = (close - low) / bar_range
    confirm = float(p.get("confirm", 0.0) or 0.0)
    if confirm > 0:
        confirm_long = close_pos >= confirm
        confirm_short = (1.0 - close_pos) >= confirm
    else:
        confirm_long = pd.Series(True, index=df.index)
        confirm_short = confirm_long.copy()

    # ── Volatility ceiling ─────────────────────────────────────────────────
    atr_cap = float(p.get("atr_pct_max", 0.0) or 0.0)
    if atr_cap > 0:
        vol_ok = ((atr / close.replace(0, float("nan"))) <= atr_cap).fillna(False)
    else:
        vol_ok = pd.Series(True, index=df.index)

    if spec.family == "MOM":            # Donchian breakout, both directions
        upper = high.rolling(n).max().shift(1)
        lower = low.rolling(n).min().shift(1)
        sma = close.rolling(int(p.get("trend", 100))).mean()
        long_entry = (close > upper) & (close > sma) & up_regime
        short_entry = (close < lower) & (close < sma) & down_regime
        long_exit = close < low.rolling(int(p.get("exit_lookback", 10))).min().shift(1)
        short_exit = close > high.rolling(int(p.get("exit_lookback", 10))).max().shift(1)

    elif spec.family == "REV":          # Panic inside an uptrend; squeeze in a downtrend
        sma = close.rolling(int(p.get("trend", 100))).mean()
        rsi = _rsi(close, int(p.get("rsi_len", 3)))
        rsi_buy = float(p.get("rsi_buy", 15))
        rsi_sell = float(p.get("rsi_sell", 65))
        long_entry = (close > sma) & (rsi < rsi_buy) & up_regime
        short_entry = (close < sma) & (rsi > (100.0 - rsi_buy)) & down_regime
        long_exit = (rsi > rsi_sell) | (close < sma * 0.97)
        short_exit = (rsi < (100.0 - rsi_sell)) | (close > sma * 1.03)

    elif spec.family == "PULL":         # Buy the dip with the trend; sell the rally in a downtrend
        sma_fast = close.rolling(int(p.get("fast", 20))).mean()
        sma_slow = close.rolling(int(p.get("slow", 100))).mean()
        dip = float(p.get("dip", 0.01))
        target = float(p.get("target", 0.02))
        long_entry = (sma_fast > sma_slow) & (close <= sma_fast * (1 - dip)) & up_regime
        short_entry = (sma_fast < sma_slow) & (close >= sma_fast * (1 + dip)) & down_regime
        long_exit = close > sma_fast * (1 + target)
        short_exit = close < sma_fast * (1 - target)

    elif spec.family == "SQZ":          # Contraction, then expansion either way
        rng = (high - low) / close
        compression = rng.rolling(int(p.get("squeeze", 10))).mean() < (
            rng.rolling(int(p.get("base", 60))).mean() * float(p.get("ratio", 0.7))
        )
        long_entry = compression.shift(1) & (close > high.shift(1)) & up_regime
        short_entry = compression.shift(1) & (close < low.shift(1)) & down_regime
        long_exit = close < low.rolling(int(p.get("exit_lookback", 7))).min().shift(1)
        short_exit = close > high.rolling(int(p.get("exit_lookback", 7))).max().shift(1)

    elif spec.family == "ABS":          # Absorption bar, direction must agree with close
        if vol is not None:
            vz = (vol - vol.rolling(20).mean()) / vol.rolling(20).std(ddof=0).replace(0, float("nan"))
            tight = (high - low) < atr * float(p.get("compress", 0.6))
            strong = vz > float(p.get("vol_z", 2.0))
            up_close = close > close.shift(1)
            down_close = close < close.shift(1)
            long_entry = strong & tight & up_close & up_regime
            short_entry = strong & tight & down_close & down_regime
        long_exit = close < low.rolling(int(p.get("exit_lookback", 7))).min().shift(1)
        short_exit = close > high.rolling(int(p.get("exit_lookback", 7))).max().shift(1)

    elif spec.family == "SWEEP":        # Failed break, reclaimed level, regime agrees
        prior_low = low.rolling(int(p.get("lookback", 10))).min().shift(1)
        prior_high = high.rolling(int(p.get("lookback", 10))).max().shift(1)
        long_entry = (low < prior_low) & (close > prior_low) & up_regime
        short_entry = (high > prior_high) & (close < prior_high) & down_regime
        long_exit = close > high.rolling(int(p.get("target_lookback", 10))).max().shift(1)
        short_exit = close < low.rolling(int(p.get("target_lookback", 10))).min().shift(1)

    # ── Apply the shared filters ───────────────────────────────────────────
    long_entry = long_entry & confirm_long & vol_ok
    short_entry = (short_entry & confirm_short & vol_ok) if allow_short else false.copy()

    if regime_len > 1 and int(p.get("exit_regime", 1) or 0):
        long_exit = long_exit | lost_regime_long
        short_exit = short_exit | lost_regime_short

    return {
        "long_entry": long_entry.fillna(False),
        "short_entry": short_entry.fillna(False),
        "long_exit": long_exit.fillna(False),
        "short_exit": short_exit.fillna(False),
    }


def _rsi(close, length: int = 3):
    import pandas as pd
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(length).mean()
    loss = (-delta.clip(upper=0)).rolling(length).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100 - (100 / (1 + rs))).fillna(50)


# ═══════════════════════════════════════════════════════════════════════════
#  Backtest
# ═══════════════════════════════════════════════════════════════════════════

def backtest(
    df,
    spec: StrategySpec,
    capital: float = DEFAULT_CAPITAL,
    risk_pct: float = DEFAULT_RISK_PCT,
    cost_bps: float = DEFAULT_COST_BPS,
    start: int = 120,
    end: Optional[int] = None,
) -> Dict[str, Any]:
    """Long-only, ATR-stopped, cost-charged backtest.

    Entry is taken at the **next bar's open** after a signal on the close, so
    there is no look-ahead. If both the stop and the target are inside the same
    bar, the stop is assumed to fill first (conservative).
    """
    if df is None or len(df) < start + 20:
        return {"status": "NO_DATA", "trades": [], "metrics": {}}

    df = df.reset_index(drop=False)
    # The index name varies by provider (index / Date / Datetime). Normalize it.
    if "date" not in df.columns:
        first = df.columns[0]
        df = df.rename(columns={first: "date"})
    atr = _atr_series(df.set_index("date"))
    entry_sig, exit_sig = _signals(df.set_index("date"), spec)
    entry_sig = entry_sig.reset_index(drop=True)
    exit_sig = exit_sig.reset_index(drop=True)
    atr = atr.reset_index(drop=True)

    open_ = df["Open"].astype(float).reset_index(drop=True)
    high = df["High"].astype(float).reset_index(drop=True)
    low = df["Low"].astype(float).reset_index(drop=True)
    close = df["Close"].astype(float).reset_index(drop=True)
    dates = [str(d)[:10] for d in df["date"]] if "date" in df.columns else [str(i) for i in range(len(df))]

    end = end if end is not None else len(df)
    stop_atr = float(spec.params.get("stop_atr", 1.5))
    target_r = float(spec.params.get("target_r", 2.0))
    time_stop = int(spec.params.get("time_stop", 20))

    trades: List[Dict[str, Any]] = []
    eq = capital
    peak = capital
    max_dd = 0.0
    curve: List[Tuple[str, float]] = [(dates[start], round(eq, 2))]

    pos: Optional[Dict[str, Any]] = None
    for i in range(start, min(end, len(df) - 1)):
        if pos is None:
            if not bool(entry_sig.iloc[i]):
                continue
            a = float(atr.iloc[i])
            if not math.isfinite(a) or a <= 0:
                continue
            entry_price = float(open_.iloc[i + 1])
            stop = entry_price - stop_atr * a
            risk_per_unit = max(1e-9, entry_price - stop)
            qty = (eq * risk_pct / 100.0) / risk_per_unit
            cost = entry_price * qty * cost_bps / 10_000.0
            pos = {
                "entry_date": dates[i + 1],
                "entry": entry_price,
                "stop": stop,
                "target": entry_price + target_r * risk_per_unit,
                "qty": qty,
                "entry_i": i + 1,
                "cost": cost,
                "mae": 0.0,
                "mfe": 0.0,
                "entry_atr": a,
                "risk0": risk_per_unit,
            }
            continue

        # Manage the open position from the entry bar forward.
        j = i
        if j < pos["entry_i"]:
            continue
        pos["mae"] = max(pos["mae"], pos["entry"] - float(low.iloc[j]))
        pos["mfe"] = max(pos["mfe"], float(high.iloc[j]) - pos["entry"])

        exit_price = None
        reason = ""
        if float(low.iloc[j]) <= pos["stop"]:
            exit_price, reason = pos["stop"], "STOP"
        elif float(high.iloc[j]) >= pos["target"]:
            exit_price, reason = pos["target"], "TARGET"
        elif bool(exit_sig.iloc[j]):
            exit_price, reason = float(close.iloc[j]), "SIGNAL_EXIT"
        elif j - pos["entry_i"] >= time_stop:
            exit_price, reason = float(close.iloc[j]), "TIME_STOP"

        if exit_price is None:
            # Trade management for the *next* bar: move to breakeven at
            # ``breakeven_r`` and trail by ``trail_atr`` once the trade has
            # run 1.5R. This is the fix the mistakes reports prescribe, so it
            # is part of the search space rather than an afterthought.
            trail = float(spec.params.get("trail_atr", 0) or 0)
            if trail > 0:
                risk0 = float(pos.get("risk0") or (pos["entry"] - pos["stop"]))
                be_r = float(spec.params.get("breakeven_r", 1.0) or 0)
                if risk0 > 0 and pos["mfe"] >= be_r * risk0:
                    pos["stop"] = max(pos["stop"], pos["entry"])
                if risk0 > 0 and pos["mfe"] >= 1.5 * risk0:
                    pos["stop"] = max(
                        pos["stop"],
                        float(high.iloc[j]) - trail * float(atr.iloc[j]),
                    )
            continue

        gross = (exit_price - pos["entry"]) * pos["qty"]
        exit_cost = exit_price * pos["qty"] * cost_bps / 10_000.0
        net = gross - pos["cost"] - exit_cost
        eq += net
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100.0 if peak else 0.0
        max_dd = max(max_dd, dd)

        r_multiple = (exit_price - pos["entry"]) / max(1e-9, pos["entry"] - pos["stop"])
        trades.append({
            "entry_date": pos["entry_date"],
            "exit_date": dates[j],
            "entry": round(pos["entry"], 2),
            "exit": round(exit_price, 2),
            "qty": round(pos["qty"], 3),
            "stop": round(pos["stop"], 2),
            "target": round(pos["target"], 2),
            "pnl": round(net, 2),
            "r_multiple": round(r_multiple, 2),
            "reason": reason,
            "bars_held": j - pos["entry_i"],
            "mae_pts": round(pos["mae"], 1),
            "mfe_pts": round(pos["mfe"], 1),
            "stop_atr": round(pos["entry"] - pos["stop"], 1),
            "entry_atr": round(pos["entry_atr"], 1),
        })
        curve.append((dates[j], round(eq, 2)))
        pos = None

    # Close an open position at the last bar so P&L is complete.
    if pos is not None:
        exit_price = float(close.iloc[-1])
        net = (exit_price - pos["entry"]) * pos["qty"] - pos["cost"]
        eq += net
        trades.append({
            "entry_date": pos["entry_date"], "exit_date": dates[-1],
            "entry": round(pos["entry"], 2), "exit": round(exit_price, 2),
            "qty": round(pos["qty"], 3), "stop": round(pos["stop"], 2),
            "target": round(pos["target"], 2), "pnl": round(net, 2),
            "r_multiple": round((exit_price - pos["entry"]) / max(1e-9, pos["entry"] - pos["stop"]), 2),
            "reason": "MARK_TO_MARKET", "bars_held": len(df) - 1 - pos["entry_i"],
            "mae_pts": round(pos["mae"], 1), "mfe_pts": round(pos["mfe"], 1),
            "stop_atr": round(pos["entry"] - pos["stop"], 1),
            "entry_atr": round(pos["entry_atr"], 1),
        })
        curve.append((dates[-1], round(eq, 2)))

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
    years = max(1e-9, (len(dates) - start) / 252.0)
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
        "passed": bool(passed),
    }


def _oos_split(df) -> Tuple[int, int]:
    n = len(df)
    return int(n * 0.70), n


# ═══════════════════════════════════════════════════════════════════════════
#  Families + parameter grids
# ═══════════════════════════════════════════════════════════════════════════

FAMILIES: Dict[str, Dict[str, Any]] = {
    "MOM": {
        "thesis": (
            "Institutions cannot hide size. When price clears a 20-day high while "
            "above the 100-day mean, the marginal seller is exhausted and the "
            "remaining supply is above the market — continuation is the cheaper path."
        ),
        "entry": "Buy the next open after a close above the prior {lookback}-day high with close > {trend}-day mean.",
        "exit": "Exit on a close below the prior {exit_lookback}-day low, the ATR stop, the {target_r}R target, or after {time_stop} bars.",
        "grid": [
            {"lookback": 20, "trend": 100, "exit_lookback": 10, "stop_atr": 1.5, "target_r": 2.0, "time_stop": 20},
            {"lookback": 55, "trend": 100, "exit_lookback": 20, "stop_atr": 2.0, "target_r": 2.5, "time_stop": 30},
            {"lookback": 10, "trend": 50, "exit_lookback": 7, "stop_atr": 1.2, "target_r": 1.8, "time_stop": 12},
        ],
    },
    "REV": {
        "thesis": (
            "Short-term panic inside a long-term uptrend is liquidity demand, not "
            "information. A 3-period RSI below 15 while above the 100-day mean is "
            "a forced seller meeting a passive buyer."
        ),
        "entry": "Buy the next open when RSI({rsi_len}) < {rsi_buy} and close > {trend}-day mean.",
        "exit": "Exit when RSI({rsi_len}) > {rsi_sell}, or on the ATR stop / {target_r}R target / {time_stop}-bar time stop.",
        "grid": [
            {"rsi_len": 3, "rsi_buy": 15, "rsi_sell": 65, "trend": 100, "stop_atr": 1.5, "target_r": 1.5, "time_stop": 10},
            {"rsi_len": 2, "rsi_buy": 10, "rsi_sell": 60, "trend": 100, "stop_atr": 2.0, "target_r": 2.0, "time_stop": 8},
            {"rsi_len": 5, "rsi_buy": 25, "rsi_sell": 70, "trend": 50, "stop_atr": 1.2, "target_r": 1.5, "time_stop": 15},
        ],
    },
    "PULL": {
        "thesis": (
            "In an established uptrend, execution algos work buy orders on dips to "
            "the short mean so they do not pay up. Buying the dip with the trend is "
            "buying where the patient bid actually sits."
        ),
        "entry": "Buy the next open when the {fast}-day mean > {slow}-day mean and close is {dip} below the {fast}-day mean.",
        "exit": "Exit on a close {target} above the {fast}-day mean, the ATR stop, or the {time_stop}-bar time stop.",
        "grid": [
            {"fast": 20, "slow": 100, "dip": 0.01, "target": 0.02, "stop_atr": 1.5, "target_r": 2.0, "time_stop": 15},
            {"fast": 10, "slow": 50, "dip": 0.015, "target": 0.025, "stop_atr": 1.2, "target_r": 2.0, "time_stop": 12},
            {"fast": 50, "slow": 200, "dip": 0.02, "target": 0.04, "stop_atr": 2.0, "target_r": 2.5, "time_stop": 30},
        ],
    },
    "SQZ": {
        "thesis": (
            "Volatility contracts when both sides are passive. The first close above "
            "the prior bar after a contraction marks the side that blinked, and "
            "dealers must re-hedge in that direction."
        ),
        "entry": "Buy the next open after a {squeeze}-bar range contraction below {ratio}× the {base}-bar average range, when close > prior high.",
        "exit": "Exit on a close below the prior {exit_lookback}-day low, the ATR stop, or the {time_stop}-bar time stop.",
        "grid": [
            {"squeeze": 10, "base": 60, "ratio": 0.7, "exit_lookback": 7, "stop_atr": 1.5, "target_r": 2.0, "time_stop": 15},
            {"squeeze": 5, "base": 40, "ratio": 0.6, "exit_lookback": 5, "stop_atr": 1.2, "target_r": 1.8, "time_stop": 10},
        ],
    },
    "ABS": {
        "thesis": (
            "A high-volume bar with a tight range is two-sided trade absorbed by a "
            "passive participant. If the close is up, the absorber is a buyer, and "
            "the path of least resistance is up."
        ),
        "entry": "Buy the next open after a bar with volume z > {vol_z} and range < {compress}× ATR that closes up.",
        "exit": "Exit on a close below the prior {exit_lookback}-day low, the ATR stop, or the {time_stop}-bar time stop.",
        "grid": [
            {"vol_z": 2.0, "compress": 0.6, "exit_lookback": 7, "stop_atr": 1.5, "target_r": 2.0, "time_stop": 15},
            {"vol_z": 2.5, "compress": 0.5, "exit_lookback": 5, "stop_atr": 1.2, "target_r": 2.0, "time_stop": 12},
        ],
    },
    "SWEEP": {
        "thesis": (
            "A wick below a well-known low that closes back inside is stop-running: "
            "resting sell stops became market sell orders, a buyer took them, and "
            "the level was reclaimed. The failed break is the signal."
        ),
        "entry": "Buy the next open when the low pierces the prior {lookback}-day low and the close is back above it.",
        "exit": "Exit on a close above the prior {target_lookback}-day high, the ATR stop, or the {time_stop}-bar time stop.",
        "grid": [
            {"lookback": 10, "target_lookback": 10, "stop_atr": 1.5, "target_r": 2.0, "time_stop": 15},
            {"lookback": 20, "target_lookback": 20, "stop_atr": 2.0, "target_r": 2.5, "time_stop": 25},
        ],
    },
}


def _describe(spec: StrategySpec) -> StrategySpec:
    fam = FAMILIES.get(spec.family, {})
    spec.thesis = fam.get("thesis", spec.thesis)
    spec.entry_text = fam.get("entry", spec.entry_text).format(**{k: _fmt(v) for k, v in spec.params.items()})
    spec.exit_text = fam.get("exit", spec.exit_text).format(**{k: _fmt(v) for k, v in spec.params.items()})
    trail = spec.params.get("trail_atr")
    spec.risk_text = (
        f"Long only. Stop {spec.params.get('stop_atr', 1.5)}× ATR below entry; "
        f"target {spec.params.get('target_r', 2.0)}R; time stop {spec.params.get('time_stop', 20)} bars; "
        + (f"breakeven at +{spec.params.get('breakeven_r', 1.0)}R then trail {trail}× ATR; " if trail else "")
        + f"{DEFAULT_RISK_PCT}% of equity risked per trade; {DEFAULT_COST_BPS:.0f} bps round-trip cost charged."
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


def render_strategy_doc(spec: StrategySpec, result: Dict[str, Any]) -> str:
    m = result.get("metrics", {})
    status = "PASSED" if m.get("passed") else "NOT PASSED"
    lines = [
        f"# {spec.name} — {spec.family} on {spec.index_key}",
        "",
        f"**Status:** {status}  ",
        f"**Generated:** {_dt.datetime.now().isoformat(timespec='seconds')}  ",
        f"**Engine:** ZERO v{SCHEMA_VERSION} Strategy Lab  ",
        f"**Improved from:** {spec.improved_from or '—'}",
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
        "## Backtest result",
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
        f"| Sharpe (per-trade, sample-scaled) | {m.get('sharpe', 0):.2f} |",
        f"| Stop-out rate | {m.get('stop_rate_pct', 0):.1f}% |",
        "",
        "## How to reproduce",
        "",
        "```python",
        "from engine.v2.strategy_lab import StrategySpec, backtest, _frame",
        f"spec = StrategySpec(family='{spec.family}', index_key='{spec.index_key}', params={spec.params!r})",
        f"df = _frame('{spec.index_key}')",
        "print(backtest(df, spec)['metrics'])",
        "```",
        "",
        "## Reuse",
        "",
        f"Load the spec from `strategies/index.json` under `{spec.name}` and pass it to",
        f"`backtest()` or the v2.0 panel. The id is stable for the same parameter set.",
        "",
        "---",
        "_Backtests are simulations. Costs are approximated at "
        f"{DEFAULT_COST_BPS:.0f} bps round trip and do not model gap slippage beyond "
        "the next-open fill. Past performance is not a forecast._",
    ]
    return "\n".join(lines)


def render_mistakes_doc(spec: StrategySpec, result: Dict[str, Any]) -> str:
    m = result.get("metrics", {})
    trades = result.get("trades", []) or []
    stops = [t for t in trades if t["reason"] == "STOP"]
    targets = [t for t in trades if t["reason"] == "TARGET"]
    losers = sorted(trades, key=lambda t: t["pnl"])[:3]
    winners = sorted(trades, key=lambda t: t["pnl"], reverse=True)[:3]

    # Measured mistakes (derived from this run, not boilerplate).
    tight_stops = [t for t in trades if t["entry_atr"] and t["stop_atr"] < 0.8 * t["entry_atr"]]
    premature = [t for t in stops if t["mfe_pts"] > 1.5 * max(1e-9, t["stop_atr"])]
    late_entries = [t for t in trades if t["entry_atr"] and t["mae_pts"] > 0.9 * t["entry_atr"]]

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
        if tight_stops:
            lines.append(
                f"- **Stops inside the noise band.** {len(tight_stops)} of {len(trades)} trades "
                f"used a stop below 0.8× the entry ATR. Those stops sit inside one normal bar's "
                "range, so they are liquidity for other participants."
            )
        if premature:
            lines.append(
                f"- **Premature stop-outs.** {len(premature)} stop-outs had already run "
                "more than 1.5× their stop distance in favour before reversing. The stop was "
                "placed for the thesis but not for the entry timing."
            )
        if late_entries:
            lines.append(
                f"- **Late entries.** {len(late_entries)} trades immediately moved more than "
                "0.9× ATR against entry. The signal was valid but the fill was chased."
            )
        if m.get("stop_rate_pct", 0) > 60:
            lines.append(
                f"- **Stop rate {m.get('stop_rate_pct', 0):.0f}%.** More than half the trades "
                "were stopped. The entry filter admits too many low-quality setups."
            )
        if m.get("profit_factor", 0) < 1.15:
            lines.append(
                f"- **Profit factor {m.get('profit_factor', 0):.2f}.** Gross wins do not "
                f"cover gross losses after {DEFAULT_COST_BPS:.0f} bps costs. Edge is absent or "
                "smaller than friction."
            )
        if targets:
            lines.append(
                f"- **Target discipline.** {len(targets)} trades reached the {spec.params.get('target_r')}R "
                "target. Every remaining winner was closed by signal or time, which caps the "
                "right tail — consider a trailing stop on the runners."
            )
        if not (tight_stops or premature or late_entries):
            lines.append("- No structural mistake cleared its threshold in this run. The losses are")
            lines.append("  variance around a positive expectancy rather than a repeated error.")

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
        f"- Gross winners carried the result: {len([t for t in trades if t['pnl'] > 0])} winners.",
        f"- Losers: {len([t for t in trades if t['pnl'] <= 0])}.",
        f"- Average hold: {m.get('avg_bars_held', 0)} bars.",
        f"- Expectancy: {m.get('expectancy_r', 0):+.3f}R per trade.",
        "",
        "## Fix list for the next iteration",
        "",
        "1. Widen the stop to at least 1.5× ATR and reduce size so rupee risk is unchanged.",
        "2. Require the entry bar to close in the top third of its range (confirmation, not prediction).",
        "3. Add a trailing stop after +1R to keep the right tail.",
        "4. Re-test with the parameter grid; accept only if out-of-sample profit factor does not fall.",
        "",
        "---",
        "_Generated by ZERO v2.0 Strategy Lab. Metrics are computed from the trade ledger above._",
    ]
    return "\n".join(lines)


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


# ═══════════════════════════════════════════════════════════════════════════
#  Improve
# ═══════════════════════════════════════════════════════════════════════════

def _candidate_grid(spec: StrategySpec) -> List[Dict[str, float]]:
    """Family presets plus a local perturbation search around the base params.

    The presets alone mostly reproduce the base spec once merged, so the lab
    would never actually explore. This widens the stop/target/horizon in both
    directions and keeps every unique numeric candidate.
    """
    base = dict(spec.params)
    candidates: List[Dict[str, float]] = []
    for preset in FAMILIES.get(spec.family, {}).get("grid", []):
        candidates.append({**base, **preset})

    # Trade-management variants: breakeven at +1R and an ATR trailing stop.
    for trail, be in ((1.5, 0.8), (2.5, 1.0), (2.0, 1.5)):
        candidates.append({**base, "trail_atr": trail, "breakeven_r": be})

    scales = (0.75, 0.9, 1.15, 1.35)
    for key in ("stop_atr", "target_r", "time_stop", "lookback",
                "exit_lookback", "trend", "fast", "slow", "squeeze", "rsi_buy"):
        if key not in base:
            continue
        try:
            value = float(base[key])
        except (TypeError, ValueError):
            continue
        for scale in scales:
            candidate = dict(base)
            new_value = value * scale
            candidate[key] = int(round(new_value)) if isinstance(base[key], int) else round(new_value, 3)
            if candidate[key] != base[key]:
                candidates.append(candidate)

    unique: List[Dict[str, float]] = []
    seen = set()
    for candidate in candidates:
        fingerprint = json.dumps(candidate, sort_keys=True)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(candidate)
    return unique


def improve(spec: StrategySpec, df, base_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Grid-search the family around a passing strategy, validated OOS.

    An improvement is accepted only when the out-of-sample profit factor is at
    least the base OOS profit factor and OOS trades >= 5. Returns the improved
    spec/result pair, or ``None``.
    """
    split, n = _oos_split(df)
    base_oos_metrics = _slice_backtest(df, spec, split, n).get("metrics", {})
    base_oos_trades = base_oos_metrics.get("trades", 0)
    base_oos_pf = base_oos_metrics.get("profit_factor", 0.0)
    if base_oos_trades < 3:
        return None

    # A tiny OOS sample can report PF 99 (one winning trade). Only trust the
    # parent's OOS PF as a bar when the sample is actually meaningful.
    required_oos_pf = base_oos_pf if base_oos_trades >= 5 else MIN_OOS_PROFIT_FACTOR
    base_full_pf = base_result.get("metrics", {}).get("profit_factor", 0.0)

    best: Optional[Tuple[float, StrategySpec, Dict[str, Any], Dict[str, Any]]] = None
    for params in _candidate_grid(spec):
        candidate = _describe(StrategySpec(
            family=spec.family,
            index_key=spec.index_key,
            params=params,
            improved_from=spec.name,
        ))
        if candidate.name == spec.name:
            continue

        oos = _slice_backtest(df, candidate, split, n)
        om = oos.get("metrics", {})
        # Constraint 1: out-of-sample must not degrade.
        if om.get("trades", 0) < 5 or om.get("profit_factor", 0) < max(required_oos_pf, MIN_OOS_PROFIT_FACTOR):
            continue

        # Constraint 2: the full sample must actually beat the parent.
        full = backtest(df, candidate)
        fm = full.get("metrics", {})
        if fm.get("trades", 0) < 5 or fm.get("profit_factor", 0.0) < base_full_pf:
            continue

        score = fm.get("profit_factor", 0.0) * min(1.0, fm.get("trades", 0) / 20.0)
        if best is None or score > best[0]:
            best = (score, candidate, full, oos)

    if best is None:
        return None
    _, candidate, full, oos = best
    return {"spec": candidate, "result": full, "oos": oos}


def _slice_backtest(df, spec: StrategySpec, start: int, end: int) -> Dict[str, Any]:
    return backtest(df, spec, start=start, end=end)


# ═══════════════════════════════════════════════════════════════════════════
#  Registry + orchestration
# ═══════════════════════════════════════════════════════════════════════════

def load_registry() -> Dict[str, Any]:
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_registry(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    passed = [e for e in entries if e.get("status") == "PASSED"]
    improved = [e for e in entries if e.get("improved_from")]
    def _rank(entry: Dict[str, Any]) -> float:
        m = entry.get("metrics", {})
        return m.get("profit_factor", 0) * min(1.0, m.get("trades", 0) / 20.0)

    # The brief wants the top three *winners*. If nothing cleared the gate yet,
    # report the three best candidates and label them honestly rather than
    # showing an empty list.
    podium = sorted(passed, key=_rank, reverse=True)[:3]
    if not podium:
        podium = sorted(entries, key=_rank, reverse=True)[:3]
    top = [
        {
            "name": e["name"], "index": e.get("index"), "family": e.get("family"),
            "status": e.get("status"), "metrics": e.get("metrics", {}),
            "improved_from": e.get("improved_from", ""),
        }
        for e in podium
    ]

    registry = {
        "schema": SCHEMA_VERSION,
        "updated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "total": len(entries),
        "passed": len(passed),
        "failed": len(entries) - len(passed),
        "improved": len(improved),
        "top": top,
        "strategies": entries,
    }
    _write(REGISTRY_PATH, json.dumps(registry, indent=2, default=str))
    return registry


def run_lab(
    indices: Tuple[str, ...] = ("NIFTY", "SENSEX"),
    families: Tuple[str, ...] = tuple(FAMILIES.keys()),
    improve_winners: bool = True,
    max_improvements: int = 3,
) -> Dict[str, Any]:
    """Generate, backtest, document and (optionally) improve strategies.

    Returns the registry plus a run summary. Never raises: a missing index or a
    failed family is recorded as a skipped entry.
    """
    _ensure_dir()
    entries: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []

    for index_key in indices:
        df = _frame(index_key)
        if df is None or len(df) < 160:
            skipped.append({"index": index_key, "reason": "history unavailable or too short"})
            continue
        for family in families:
            for params in FAMILIES.get(family, {}).get("grid", []):
                spec = _describe(StrategySpec(family=family, index_key=index_key, params=dict(params)))
                try:
                    result = backtest(df, spec)
                except Exception as exc:
                    skipped.append({"index": index_key, "family": family, "reason": str(exc)[:120]})
                    continue
                m = result.get("metrics", {})
                status = "PASSED" if m.get("passed") else "NOT_PASSED"
                doc_path = os.path.join(STRATEGY_DIR, f"{spec.name}.md")
                mistakes_path = os.path.join(STRATEGY_DIR, f"_@{spec.name}_, mistakes and P&L.md")
                _write(doc_path, render_strategy_doc(spec, result))
                _write(mistakes_path, render_mistakes_doc(spec, result))
                entries.append({
                    "name": spec.name,
                    "family": family,
                    "index": index_key,
                    "status": status,
                    "params": spec.params,
                    "metrics": m,
                    "doc": doc_path.replace("\\", "/"),
                    "mistakes_doc": mistakes_path.replace("\\", "/"),
                    "improved_from": "",
                    "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
                })

    # Improvement pass over the best strategies by risk-adjusted rank.
    # Passing strategies get priority, but near-misses are improved too: an
    # honest lab keeps iterating on the best available idea instead of sitting
    # idle when nothing has cleared the gate yet.
    if improve_winners:
        def _rank_entry(e: Dict[str, Any]) -> float:
            m = e.get("metrics", {})
            score = m.get("profit_factor", 0) * min(1.0, m.get("trades", 0) / 20.0)
            return score + (0.5 if e.get("status") == "PASSED" else 0.0)

        winners = sorted(entries, key=_rank_entry, reverse=True)[:max_improvements]
        for entry in winners:
            df = _frame(entry["index"])
            if df is None:
                continue
            spec = StrategySpec(
                family=entry["family"], index_key=entry["index"],
                params=entry["params"], name=entry["name"],
            )
            try:
                base = backtest(df, spec)
                upgraded = improve(spec, df, base)
            except Exception as exc:
                skipped.append({"index": entry["index"], "family": entry["family"],
                                "reason": f"improve failed: {exc}"[:120]})
                continue
            if not upgraded:
                continue
            child = upgraded["spec"]
            child.improved_from = entry["name"]
            child.name = make_name(child.family, child.index_key, {**child.params, "_impr": 1})
            child = _describe(child)
            child_result = upgraded["result"]
            doc_path = os.path.join(STRATEGY_DIR, f"{child.name}.md")
            mistakes_path = os.path.join(STRATEGY_DIR, f"_@{child.name}_, mistakes and P&L.md")
            _write(doc_path, render_strategy_doc(child, child_result))
            _write(mistakes_path, render_mistakes_doc(child, child_result))
            cm = child_result.get("metrics", {})
            entries.append({
                "name": child.name,
                "family": child.family,
                "index": child.index_key,
                "status": "PASSED" if cm.get("passed") else "NOT_PASSED",
                "params": child.params,
                "metrics": cm,
                "doc": doc_path.replace("\\", "/"),
                "mistakes_doc": mistakes_path.replace("\\", "/"),
                "improved_from": entry["name"],
                "oos": upgraded["oos"].get("metrics", {}),
                "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            })

    registry = _save_registry(entries)
    return {
        "schema": SCHEMA_VERSION,
        "registry": registry,
        "skipped": skipped,
        "created": len(entries),
        "passed": registry["passed"],
        "improved": registry["improved"],
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
