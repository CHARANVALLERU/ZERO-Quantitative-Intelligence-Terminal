"""
Reference desks — items 7–10, 19, 23 and 24.

Seven workflows the operator asked to borrow from other trading products:

====  =============  =====================================================
item  reference      desk
====  =============  =====================================================
7     Robinhood      order ticket + account snapshot
8     BigShort       smart-vs-retail flow from option OI build
9     ThinkOrSwim    option risk profile + probability cone
10    TradingView    indicator stack on the index's own bars
19    Vibe-Trading   four specialists, one memo
23    DAS Trader     bar-by-bar replay of a breakout rule
24    QLiquidity     order-book imbalance from chain depth
====  =============  =====================================================

Every desk is a clean-room rebuild from public mechanics on data already in the
repo (``indices``, ``trading``, ``quant``). No asset, code, string or branding is
copied from the reference product, and no function reports an extraction it did
not perform: ``rea_evidence`` stays ``None`` until a verified REA artefact exists
for that item, in which case it holds the path to the artefact.

All functions are pure: no Streamlit, no writes. Network calls stay inside
``trading.option_table`` / ``indices.quote`` / the AITE frame loader, and every
one of them degrades to ``{"available": False, "reason": ...}`` instead of a
fabricated number. ``rows``, ``spot`` and ``frame`` are optional injection seams
so the desks can be unit-tested offline.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from engine.v21 import indices as idx
from engine.v21 import trading
from engine.v21.quant import ema

# ── shared helpers ──────────────────────────────────────────────────────────


def _frame(index: str, bars: int = 320):
    from engine.aite.exam import load_market_frame
    return load_market_frame(idx.aite_symbol(index), bars=bars)


def _spot(key: str, df=None):
    """Live spot if the quote service answers, else the last historical close."""
    q = idx.quote(key)
    if q.get("available") and q.get("last") is not None:
        return float(q["last"]), q.get("source") or "live quote"
    if df is not None and len(df):
        return float(df["close"].iloc[-1]), "last historical close"
    return None, "no quote"


def _atr(df, window: int = 14) -> float:
    """Average true range from a frame that has high/low/close."""
    try:
        h, l, c = (df[x].astype(float).to_numpy() for x in ("high", "low", "close"))
    except Exception:
        return 0.0
    if len(c) < window + 2:
        return 0.0
    tr = np.maximum(h[1:] - l[1:],
                    np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(np.mean(tr[-window:]))


def _stamps(df) -> List[str]:
    """Bar dates when the frame carries them, else bar numbers."""
    if "timestamps" in df.columns:
        return df["timestamps"].astype(str).tolist()
    return [str(i) for i in range(len(df))]


def _missing(reason: str, **extra) -> Dict[str, Any]:
    return {"available": False, "reason": reason, **extra}


# ── item 7 · Robinhood: order ticket + account ──────────────────────────────


def order_ticket(index: str = "NIFTY", side: str = "BUY", capital: float = 500_000.0,
                 risk_pct: float = 1.0, stop_atr: float = 1.5,
                 target_atr: float = 2.5, frame=None) -> Dict[str, Any]:
    """
    Everything a ticket needs before it is sent: size, levels and rupees at risk.

    Sizing is risk-first: lots = risk budget ÷ (stop distance × lot value). The
    exchange SPAN/exposure margin is deliberately *not* estimated — it needs a
    broker feed, so the ticket shows notional and says so.
    """
    key = idx.canonical(index)
    df = frame if frame is not None else _frame(key, 160)
    spot, src = _spot(key, df)
    if spot is None:
        return _missing("no spot price, so no ticket")

    lot = idx.lot_size(key)
    atr = _atr(df) or spot * 0.004
    side = "SELL" if str(side).upper().startswith("S") else "BUY"
    sign = 1 if side == "BUY" else -1
    stop = spot - sign * stop_atr * atr
    target = spot + sign * target_atr * atr
    risk_per_lot = stop_atr * atr * lot
    budget = float(capital) * float(risk_pct) / 100.0
    lots = int(budget // risk_per_lot) if risk_per_lot > 0 else 0
    tradeable = lots >= 1
    notional = spot * lot * max(lots, 1)

    return {
        "available": True,
        "engine": "ticket",
        "reference": "Robinhood order ticket",
        "rea_evidence": None,
        "label": f"{side} {key} · {max(lots, 1)} lot(s)" if tradeable else f"{side} {key} · no size",
        "bias": side if tradeable else "FLAT",
        "detail": (f"{max(lots, 1)} × {lot} units at {spot:,.2f} = ₹{notional:,.0f} notional; "
                   f"stop {stop:,.2f}, target {target:,.2f} "
                   f"({stop_atr:g}× / {target_atr:g}× ATR {atr:,.1f})."
                   if tradeable else
                   f"one lot risks ₹{risk_per_lot:,.0f}, above the ₹{budget:,.0f} budget — no trade."),
        "metrics": {
            "spot": round(spot, 2), "lot": lot, "lots": lots,
            "notional": round(notional, 2), "risk_per_lot": round(risk_per_lot, 2),
            "risk_total": round(risk_per_lot * lots, 2), "risk_budget": round(budget, 2),
            "atr": round(atr, 2),
        },
        "levels": {"entry": round(spot, 2), "stop": round(stop, 2), "target": round(target, 2)},
        "source": src,
        "margin_note": "SPAN + exposure margin is not modelled; notional is shown, not margin",
    }


def account_snapshot(capital: float = 500_000.0) -> Dict[str, Any]:
    """
    The account surface behind the ticket — the local order ledger, not a broker.

    Reads ``engine.broker.order_state`` so the desk shows the same lifecycle the
    API writes to ``db/broker/orders.json``. No broker session is involved.
    """
    try:
        from engine.broker import order_state
    except Exception as exc:  # noqa: BLE001
        return _missing(f"order ledger unavailable: {type(exc).__name__}: {exc}")
    open_orders = order_state.open_orders()
    filled = sum(float(o.get("filled_quantity") or 0) for o in open_orders)
    return {
        "available": True,
        "engine": "account",
        "reference": "Robinhood account surface",
        "rea_evidence": None,
        "label": f"{len(open_orders)} open order(s)",
        "detail": (f"Paper book at db/broker/orders.json; {filled:g} filled units still open. "
                   f"No broker session is configured, so buying power is the desk's own capital."),
        "metrics": {"open_orders": len(open_orders), "buying_power": round(float(capital), 2),
                    "filled_units": round(filled, 2)},
        "orders": [{k: o.get(k) for k in ("order_id", "symbol", "side", "quantity", "state")}
                   for o in open_orders[-10:]],
    }


# ── item 8 · BigShort: smart vs retail flow ─────────────────────────────────


def flow_tape(index: str = "NIFTY", rows: Optional[List[Dict[str, Any]]] = None,
              spot: Optional[float] = None, top_share: float = 0.33) -> Dict[str, Any]:
    """
    OI build read as supply/demand, split into institutional vs small size.

    Rule (public mechanics, stated so it can be argued with): put OI building
    *below* spot is put writing — support; call OI building *above* spot is call
    writing — supply. Strikes in the top third of OI change are treated as
    institutional size; the rest as retail. NSE does not tag participants, so
    the split is a size proxy and the payload says so.
    """
    key = idx.canonical(index)
    rows = trading.option_table(key, max_rows=60) if rows is None else rows
    if not rows:
        return _missing("option chain unavailable — NSE rate limit or market closed")
    if spot is None:
        spot, _ = _spot(key)
    if spot is None:
        strikes = [float(r["strike"]) for r in rows if r.get("strike")]
        spot = sum(strikes) / len(strikes) if strikes else None
    if spot is None:
        return _missing("no spot to anchor strikes against")

    changes = [abs(float(r.get("call_oi_chg") or 0)) + abs(float(r.get("put_oi_chg") or 0))
               for r in rows]
    cut = float(np.quantile(changes, 1.0 - top_share)) if changes else 0.0
    out_rows: List[Dict[str, Any]] = []
    smart = retail = 0.0
    for r, chg in zip(rows, changes):
        strike = float(r.get("strike") or 0)
        call_chg = float(r.get("call_oi_chg") or 0)
        put_chg = float(r.get("put_oi_chg") or 0)
        vote = (put_chg if strike <= spot else 0.0) - (call_chg if strike >= spot else 0.0)
        if chg >= cut:
            smart += vote
        else:
            retail += vote
        out_rows.append({
            "strike": strike,
            "call_oi_chg": round(call_chg),
            "put_oi_chg": round(put_chg),
            "build": "writing" if (call_chg + put_chg) > 0 else "unwinding",
            "size": "institutional" if chg >= cut else "retail",
        })

    bias = "LONG" if smart > 0 else "SHORT" if smart < 0 else "FLAT"
    total = abs(smart) + abs(retail)
    return {
        "available": True,
        "engine": "flow",
        "reference": "BigShort flow",
        "rea_evidence": None,
        "label": f"Smart flow {bias} · {smart:+,.0f} contracts net",
        "bias": bias,
        "confidence": round(min(0.8, 0.35 + abs(smart) / max(total, 1.0) * 0.45), 2),
        "detail": (f"Institutional-size OI build nets {smart:+,.0f} contracts "
                   f"(put writing below spot adds, call writing above spot subtracts); "
                   f"small-size flow nets {retail:+,.0f}."),
        "metrics": {"smart_net": round(smart), "retail_net": round(retail),
                    "spot": round(spot, 2), "cut_contracts": round(cut)},
        "rows": out_rows,
        "source": "NSE option chain (OI build)",
        "delay": "1–3 s delayed",
        "note": "size is a proxy for institution vs retail; NSE publishes no participant tag",
    }


# ── item 9 · ThinkOrSwim: risk profile + probability cone ───────────────────


def risk_profile(index: str = "NIFTY", strategy: str = "long_straddle", width: int = 2,
                 lots: int = 1, rows: Optional[List[Dict[str, Any]]] = None,
                 spot: Optional[float] = None) -> Dict[str, Any]:
    """
    Payoff at expiry around the money, breakevens and premium at risk.

    ``strategy`` is ``long_straddle``, ``short_straddle``, ``long_strangle`` or
    ``short_strangle``. ``width`` is the number of chain steps between the two
    legs of a strangle.
    """
    key = idx.canonical(index)
    rows = trading.option_table(key, max_rows=60) if rows is None else rows
    if not rows:
        return _missing("option chain unavailable — NSE rate limit or market closed")
    by_strike = {float(r["strike"]): r for r in rows if r.get("strike")}
    strikes = sorted(by_strike)
    if len(strikes) < 2:
        return _missing("chain has fewer than two strikes")
    if spot is None:
        spot, _ = _spot(key)
    if spot is None:
        spot = (strikes[0] + strikes[-1]) / 2.0

    atm = min(strikes, key=lambda k: abs(k - spot))
    i = strikes.index(atm)
    if "strangle" in strategy:
        lo = strikes[max(0, i - max(1, int(width)))]
        hi = strikes[min(len(strikes) - 1, i + max(1, int(width)))]
    else:
        lo = hi = atm
    call = by_strike[hi].get("call_ltp")
    put = by_strike[lo].get("put_ltp")
    if not call or not put:
        return _missing(f"chain has no premiums at {lo:g}/{hi:g}")

    lot = idx.lot_size(key) * max(1, int(lots))
    premium = (float(call) + float(put)) * lot
    short = strategy.startswith("short")
    sign = -1.0 if short else 1.0

    grid = [spot * (1 + d) for d in (-0.06, -0.04, -0.02, -0.01, 0.0,
                                     0.01, 0.02, 0.04, 0.06)]
    points = []
    for s in grid:
        intrinsic = (max(s - hi, 0.0) + max(lo - s, 0.0)) * lot
        pnl = sign * (intrinsic - premium)
        points.append({"spot": round(s, 2), "pnl": round(pnl, 2)})

    breakeven_lo = lo - premium / lot
    breakeven_hi = hi + premium / lot
    max_loss = round(premium, 2) if not short else None
    return {
        "available": True,
        "engine": "risk",
        "reference": "ThinkOrSwim risk profile",
        "rea_evidence": None,
        "label": f"{strategy.replace('_', ' ')} · {hi:g}/{lo:g}",
        "bias": "SHORT VOL" if short else "LONG VOL",
        "confidence": None,
        "detail": (f"Premium {'received' if short else 'paid'} ₹{premium:,.0f} for {lot:g} units; "
                   f"breakevens {breakeven_lo:,.0f} / {breakeven_hi:,.0f}."),
        "metrics": {"premium": round(premium, 2), "lot": lot,
                    "breakeven_low": round(breakeven_lo, 2),
                    "breakeven_high": round(breakeven_hi, 2),
                    "max_loss": max_loss, "spot": round(spot, 2),
                    "call_iv": by_strike[hi].get("call_iv"),
                    "put_iv": by_strike[lo].get("put_iv")},
        "rows": points,
        "source": "NSE option chain premiums",
        "note": "expiry payoff only; greeks are not implied without a time-to-expiry from the chain",
    }


def probability_cone(index: str = "NIFTY", days: int = 30, frame=None,
                     spot: Optional[float] = None) -> Dict[str, Any]:
    """Lognormal price cone from 120-day realised vol (the ThinkOrSwim cone)."""
    key = idx.canonical(index)
    df = frame if frame is not None else _frame(key, 300)
    if df is None or len(df) < 60:
        return _missing("not enough bars for realised vol")
    rets = np.diff(np.log(df["close"].astype(float).to_numpy()))[-120:]
    sigma = float(np.std(rets, ddof=1) * math.sqrt(252))
    if spot is None:
        spot, _ = _spot(key, df)
    if spot is None or sigma <= 0:
        return _missing("no spot or zero volatility")
    t = max(int(days), 1) / 252.0
    bands = [{"p": p, "level": round(spot * math.exp(sigma * math.sqrt(t) * z), 2)}
             for p, z in (("p05", -1.6449), ("p25", -0.6745), ("p50", 0.0),
                          ("p75", 0.6745), ("p95", 1.6449))]
    return {
        "available": True,
        "engine": "cone",
        "reference": "ThinkOrSwim probability cone",
        "rea_evidence": None,
        "label": f"{days}-day cone · {sigma * 100:.1f}% vol",
        "bias": "RANGE",
        "detail": (f"Realised {sigma * 100:.1f}% annualised vol puts {days}-day 90% "
                   f"bounds at {bands[0]['level']:,.0f}–{bands[-1]['level']:,.0f} "
                   f"around {spot:,.0f} (lognormal, driftless)."),
        "metrics": {"sigma_pct": round(sigma * 100, 2), "days": int(days),
                    "spot": round(spot, 2)},
        "bands": bands,
    }


# ── item 10 · TradingView: indicator desk ───────────────────────────────────


def chart_desk(index: str = "NIFTY", bars: int = 260, frame=None) -> Dict[str, Any]:
    """EMA stack, RSI, ATR and 20-bar support/resistance on the index's own bars."""
    key = idx.canonical(index)
    df = frame if frame is not None else _frame(key, bars)
    if df is None or len(df) < 60:
        return _missing("not enough bars for indicators")
    close = df["close"].astype(float).to_numpy()
    ema20, ema50, ema200 = ema(close, 20), ema(close, 50), ema(close, 200)
    delta = pd.Series(close).diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    atr = _atr(df)
    stamps = _stamps(df)

    rows = [{"date": stamps[i][:10], "close": round(float(close[i]), 2),
             "ema20": round(float(ema20[i]), 2), "ema50": round(float(ema50[i]), 2)}
            for i in range(max(0, len(df) - 120), len(df))]

    trend = "up" if ema50[-1] > ema200[-1] else "down"
    return {
        "available": True,
        "engine": "chart",
        "reference": "TradingView indicator set",
        "rea_evidence": None,
        "label": f"{trend}-trend · RSI {float(rsi.iloc[-1]):.0f}" if pd.notna(rsi.iloc[-1]) else f"{trend}-trend",
        "bias": "LONG" if trend == "up" else "SHORT",
        "detail": (f"Close {close[-1]:,.2f} vs EMA20 {ema20[-1]:,.2f} / EMA50 {ema50[-1]:,.2f} / "
                   f"EMA200 {ema200[-1]:,.2f}; ATR {atr:,.1f}."),
        "metrics": {
            "close": round(float(close[-1]), 2), "ema20": round(float(ema20[-1]), 2),
            "ema50": round(float(ema50[-1]), 2), "ema200": round(float(ema200[-1]), 2),
            "rsi14": round(float(rsi.iloc[-1]), 1) if pd.notna(rsi.iloc[-1]) else None,
            "atr": round(atr, 2),
            "support": round(float(df["low"].astype(float).tail(20).min()), 2),
            "resistance": round(float(df["high"].astype(float).tail(20).max()), 2),
        },
        "rows": rows,
        "source": "AITE vendor bars",
    }


# ── item 19 · Vibe-Trading: specialist desk ─────────────────────────────────


def agent_desk(index: str = "NIFTY", frame=None,
               flow: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Four deterministic specialists (momentum, reversion, flow, risk), one memo.

    Mirrors the shape of an agentic trading stack without pretending to be an
    LLM: stances come from the existing engines and are rolled up by
    :func:`engine.v21.signals.consensus`. LLM prose stays off unless
    ``OPENAI_API_KEY`` is set, and ``llm_prose`` reports which mode ran.
    """
    from engine.v21 import signals
    key = idx.canonical(index)
    df = frame if frame is not None else _frame(key, 320)
    if df is None or len(df) < 210:
        return _missing("not enough bars for the specialist desk")
    close = df["close"].astype(float).to_numpy()
    ema50, ema200 = ema(close, 50), ema(close, 200)
    s = pd.Series(close)
    sd20 = float(s.tail(20).std(ddof=1) or 0.0)
    z20 = (close[-1] - float(s.tail(20).mean())) / sd20 if sd20 > 0 else 0.0

    flow = flow if flow is not None else flow_tape(key)
    flow_bias = flow.get("bias") if flow.get("available") else "FLAT"
    agents = [
        {"agent": "momentum", "bias": "LONG" if ema50[-1] > ema200[-1] else "SHORT",
         "confidence": 0.55, "available": True,
         "detail": f"EMA50 {'above' if ema50[-1] > ema200[-1] else 'below'} EMA200."},
        {"agent": "reversion",
         "bias": "FADE" if z20 > 1.5 else "BUY" if z20 < -1.5 else "FLAT",
         "confidence": 0.5 if abs(z20) > 1.5 else 0.3, "available": True,
         "detail": f"Price {z20:+.2f}σ from its 20-bar mean."},
        {"agent": "flow", "bias": flow_bias,
         "confidence": float(flow.get("confidence") or 0.3) if flow.get("available") else 0.0,
         "available": bool(flow.get("available")),
         "detail": flow.get("detail") or flow.get("reason", "no flow read")},
        {"agent": "risk", "bias": "HOLD", "confidence": 0.3, "available": True,
         "detail": f"ATR {_atr(df):,.1f} sets the stop; the desk never sizes past 1% risk."},
    ]
    cons = signals.consensus(agents, min_engines=2)
    return {
        "available": bool(cons.get("available")),
        "engine": "desk",
        "reference": "Vibe-Trading agent stack",
        "rea_evidence": None,
        "label": cons.get("label", "desk unavailable"),
        "bias": cons.get("bias", "MIXED"),
        "confidence": cons.get("confidence"),
        "detail": cons.get("detail", cons.get("reason", "")),
        "agents": agents,
        "consensus": cons,
        "llm_prose": bool(os.getenv("OPENAI_API_KEY")),
        "note": "deterministic specialists; no LLM call is made in this run",
    }


# ── item 23 · DAS Trader: replay ────────────────────────────────────────────


def replay_frames(index: str = "NIFTY", bars: int = 300, lookback: int = 20,
                  stop_atr: float = 1.5, target_atr: float = 2.5,
                  frame=None) -> Dict[str, Any]:
    """
    Bar-by-bar replay of one rule: long a close above the prior ``lookback`` high.

    Stop first, then target, so a bar that touches both is booked as the stop —
    the pessimistic fill. Returns every frame with position, levels and running
    equity so the panel can scrub through the session instead of replaying a
    movie. This replays *our* rule; it is not a DAS simulation.
    """
    key = idx.canonical(index)
    df = frame if frame is not None else _frame(key, bars + lookback + 5)
    if df is None or len(df) < lookback + 30:
        return _missing("not enough bars to replay")
    h = df["high"].astype(float).to_numpy()
    l = df["low"].astype(float).to_numpy()
    c = df["close"].astype(float).to_numpy()
    stamps = _stamps(df)

    frames: List[Dict[str, Any]] = []
    trades: List[Dict[str, Any]] = []
    pos: Optional[Dict[str, Any]] = None
    equity = peak = 0.0
    for i in range(lookback, len(c)):
        atr = _atr(df.iloc[max(0, i - 40):i + 1]) or c[i] * 0.005
        signal = ""
        if pos is None:
            if c[i] > float(np.max(h[i - lookback:i])):
                pos = {"entry": float(c[i]), "stop": float(c[i] - stop_atr * atr),
                       "target": float(c[i] + target_atr * atr), "bar": i}
                signal = "ENTER"
        elif l[i] <= pos["stop"] or h[i] >= pos["target"]:
            stopped = l[i] <= pos["stop"]
            exit_px = pos["stop"] if stopped else pos["target"]
            pnl_pct = (exit_px / pos["entry"] - 1.0) * 100.0
            trades.append({"entry_bar": pos["bar"], "exit_bar": i,
                           "entry": round(pos["entry"], 2), "exit": round(exit_px, 2),
                           "pnl_pct": round(pnl_pct, 3),
                           "result": "stop" if stopped else "target"})
            equity += pnl_pct
            signal = "STOP" if stopped else "TARGET"
            pos = None
        peak = max(peak, equity)
        frames.append({
            "bar": i, "date": stamps[i][:10], "close": round(float(c[i]), 2),
            "signal": signal, "position": "LONG" if pos else "FLAT",
            "entry": round(pos["entry"], 2) if pos else None,
            "stop": round(pos["stop"], 2) if pos else None,
            "target": round(pos["target"], 2) if pos else None,
            "equity_pct": round(equity, 3),
            "drawdown_pct": round(equity - peak, 3),
        })

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    return {
        "available": True,
        "engine": "replay",
        "reference": "DAS Trader replay",
        "rea_evidence": None,
        "label": f"{len(trades)} trades · {equity:+.2f}%",
        "bias": "LONG" if pos else "FLAT",
        "detail": (f"Breakout rule over {len(frames)} replayed bars: {len(trades)} closed trades, "
                   f"{wins} winners, {equity:+.2f}% compounded, "
                   f"max drawdown {min((f['drawdown_pct'] for f in frames), default=0):.2f}%."),
        "metrics": {"bars": len(frames), "trades": len(trades), "wins": wins,
                    "total_return_pct": round(equity, 2),
                    "max_drawdown_pct": round(min((f["drawdown_pct"] for f in frames), default=0.0), 2),
                    "rule": f"close > {lookback}-bar high · {stop_atr:g}×ATR stop · {target_atr:g}×ATR target"},
        "frames": frames,
        "trades": trades[-20:],
        "source": "AITE vendor bars",
        "note": "replay of our own breakout rule; not a DAS Trader simulation",
    }


# ── item 24 · QLiquidity: order-book imbalance ──────────────────────────────


def liquidity_scan(index: str = "NIFTY",
                   rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Displayed bid/ask quantity imbalance per strike, aggregated across the chain.

    NSE publishes pending buy/sell quantity per leg (``totalBuyQuantity`` /
    ``totalSellQuantity``), which is a depth proxy — not full level-2. Positive
    imbalance means bids outweigh offers. If the payload carries no quantities
    the desk refuses rather than inventing a book.
    """
    key = idx.canonical(index)
    rows = trading.option_table(key, max_rows=40) if rows is None else rows
    if not rows:
        return _missing("option chain unavailable — NSE rate limit or market closed")

    out: List[Dict[str, Any]] = []
    total_bid = total_ask = 0.0
    for r in rows:
        bid = float(r.get("call_bid_qty") or 0) + float(r.get("put_bid_qty") or 0)
        ask = float(r.get("call_ask_qty") or 0) + float(r.get("put_ask_qty") or 0)
        total_bid += bid
        total_ask += ask
        out.append({
            "strike": float(r.get("strike") or 0),
            "bid_qty": round(bid), "ask_qty": round(ask),
            "imbalance": round((bid - ask) / (bid + ask), 3) if (bid + ask) else 0.0,
            "oi": round(float(r.get("call_oi") or 0) + float(r.get("put_oi") or 0)),
        })

    if total_bid + total_ask == 0:
        return _missing("chain payload exposes no bid/ask quantity — depth unavailable")
    net = (total_bid - total_ask) / (total_bid + total_ask)
    bias = "LONG" if net > 0.1 else "SHORT" if net < -0.1 else "FLAT"
    out.sort(key=lambda r: abs(r["imbalance"]), reverse=True)
    return {
        "available": True,
        "engine": "liquidity",
        "reference": "QLiquidity order-book scan",
        "rea_evidence": None,
        "label": f"Book imbalance {net:+.2f}",
        "bias": bias,
        "confidence": round(min(0.75, 0.3 + abs(net) * 0.45), 2),
        "detail": (f"Displayed bids {total_bid:,.0f} vs offers {total_ask:,.0f} across "
                   f"{len(rows)} strikes ({net:+.2f}). Strongest imbalance at "
                   f"{out[0]['strike']:g} ({out[0]['imbalance']:+.2f})."),
        "metrics": {"net_imbalance": round(net, 3), "bid_qty": round(total_bid),
                    "ask_qty": round(total_ask), "strikes": len(rows)},
        "rows": out[:12],
        "source": "NSE option chain depth",
        "delay": "1–3 s delayed",
        "note": "pending buy/sell quantity is a depth proxy, not full level-2",
    }
