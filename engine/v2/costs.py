"""
engine.v2.costs — the strategy lab's cost model, wired to real Indian charges.
=============================================================================

The lab used to charge a flat ``DEFAULT_COST_BPS = 12`` per leg, a number that
was a guess ("brokerage + STT + slippage proxy") and was never reconciled with
``engine.india_costs``. That produced two errors in opposite directions:

* for a ₹20-lakh index position, ₹20 flat brokerage plus 0.5 bps slippage is far
  *cheaper* than 12 bps, so profitable strategies were under-credited;
* for a small position the flat ₹20 dominates, so cheap-looking edges were
  over-credited relative to a percentage model.

This module bridges the two: every leg is priced by ``engine.india_costs``
(brokerage + STT + exchange + SEBI + stamp + GST + adverse slippage), with the
segment chosen by the holding period, and a configurable slippage assumption.

Environment:
  ZERO_LAB_COST_MODEL   "india" (default) | "bps"
  ZERO_LAB_COST_BPS     fallback flat bps per leg (default 12)
  ZERO_LAB_SLIPPAGE_BPS adverse slippage per leg (default 3)
  ZERO_LAB_SEGMENT      equity_intraday | equity_delivery | futures | options
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from engine.india_costs import IndiaCostModel, compute_trade_cost, breakeven_points

__all__ = [
    "DEFAULT_SLIPPAGE_BPS",
    "FALLBACK_COST_BPS",
    "model_for",
    "leg_cost",
    "round_trip_cost",
    "cost_model_report",
    "segment_for_hold",
]

FALLBACK_COST_BPS = float(os.getenv("ZERO_LAB_COST_BPS", "12"))
DEFAULT_SLIPPAGE_BPS = float(os.getenv("ZERO_LAB_SLIPPAGE_BPS", "3"))
DEFAULT_SEGMENT = os.getenv("ZERO_LAB_SEGMENT", "equity_intraday")

_segment = None  # retained for backwards compatibility (was a module var)


def model_for(segment: Optional[str] = None,
              slippage_bps: Optional[float] = None) -> IndiaCostModel:
    """Cost model with the assumed slippage applied."""
    return IndiaCostModel(
        slippage_bps=float(DEFAULT_SLIPPAGE_BPS if slippage_bps is None else slippage_bps)
    )


def segment_for_hold(bars_held: int, intraday_max_bars: int = 1) -> str:
    """Intraday charges for a same-bar round trip, delivery charges beyond it."""
    return "equity_intraday" if int(bars_held) <= int(intraday_max_bars) else "equity_delivery"


def leg_cost(price: float, qty: float, side: str, segment: Optional[str] = None,
             model: Optional[IndiaCostModel] = None,
             fallback_bps: float = FALLBACK_COST_BPS) -> Dict[str, Any]:
    """
    Cost of one leg in ₹, with a full statutory breakdown.

    ``side`` is the *order* side ("buy"/"sell"), not the position side. When
    the model is disabled (``ZERO_LAB_COST_MODEL=bps``) the flat bps proxy is
    used and the breakdown is empty, so callers can keep comparing the two.
    """
    seg = segment or DEFAULT_SEGMENT
    if (os.getenv("ZERO_LAB_COST_MODEL", "india").strip().lower() == "bps"):
        total = float(price) * float(qty) * float(fallback_bps) / 10_000.0
        return {"total": total, "segment": seg, "model": "bps", "breakdown": {}}

    breakdown = compute_trade_cost(side, qty, price, seg, model or model_for(seg))
    if "error" in breakdown:
        total = float(price) * float(qty) * float(fallback_bps) / 10_000.0
        return {"total": total, "segment": seg, "model": "bps_fallback",
                "breakdown": breakdown}
    return {"total": float(breakdown["total"]), "segment": seg,
            "model": "india", "breakdown": breakdown}


def round_trip_cost(entry_price: float, exit_price: float, qty: float,
                    position_side: int, bars_held: int = 2,
                    slippage_bps: Optional[float] = None) -> Dict[str, Any]:
    """Both legs for a long (``position_side=+1``) or short (-1) trade."""
    seg = segment_for_hold(bars_held)
    model = model_for(seg, slippage_bps)
    entry_side = "buy" if position_side > 0 else "sell"
    exit_side = "sell" if position_side > 0 else "buy"
    entry = leg_cost(entry_price, qty, entry_side, seg, model)
    exit_ = leg_cost(exit_price, qty, exit_side, seg, model)
    return {
        "total": entry["total"] + exit_["total"],
        "entry": entry,
        "exit": exit_,
        "segment": seg,
        "model": entry["model"],
    }


def cost_model_report(price: float = 24000.0, qty: float = 75.0,
                      segment: str = DEFAULT_SEGMENT) -> Dict[str, Any]:
    """
    One-screen comparison used by docs/UI: what a round trip actually costs
    versus the flat 12 bps proxy the lab used to assume.
    """
    rt = round_trip_cost(price, price * 1.005, qty,
                         1, bars_held=1 if segment == "equity_intraday" else 5)
    flat = price * qty * FALLBACK_COST_BPS / 10_000.0 * 2
    return {
        "segment": rt["segment"],
        "notional": round(price * qty, 2),
        "india_costs_total": round(rt["total"], 2),
        "flat_proxy_total": round(flat, 2),
        "difference": round(rt["total"] - flat, 2),
        "breakeven_points": round(breakeven_points(price, qty, rt["segment"]), 3),
        "breakdown": rt["entry"]["breakdown"],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(cost_model_report(), indent=2, default=str))
