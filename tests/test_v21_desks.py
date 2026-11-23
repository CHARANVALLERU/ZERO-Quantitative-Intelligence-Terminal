"""Reference-desk tests — no network, no Streamlit, fixed fixtures."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.v21 import desks


# ── fixtures ───────────────────────────────────────────────────────────────

def _frame(n: int = 320, start: float = 100.0, drift: float = 0.0,
           vol: float = 0.6, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = start + np.cumsum(rng.normal(drift, vol, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    return pd.DataFrame({
        "timestamps": pd.date_range("2025-01-01", periods=n, freq="D"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": np.full(n, 1000.0),
    })


def _chain(spot: float = 25000.0, depth: bool = True,
           bid_heavy: bool = True) -> list[dict]:
    rows = []
    for k in range(24000, 26001, 100):
        below = k < spot
        row = {
            "strike": float(k),
            "call_oi": 100000, "call_oi_chg": 1000 if k > spot else 200,
            "call_iv": 14.0, "call_ltp": 60.0,
            "put_oi": 90000, "put_oi_chg": 4000 if below else 300,
            "put_iv": 15.0, "put_ltp": 55.0,
        }
        if depth:
            row.update({
                "call_bid_qty": 1200 if bid_heavy else 100,
                "call_ask_qty": 300 if bid_heavy else 1000,
                "put_bid_qty": 1100 if bid_heavy else 100,
                "put_ask_qty": 400 if bid_heavy else 1000,
            })
        rows.append(row)
    return rows


@pytest.fixture
def quote_ok(monkeypatch):
    monkeypatch.setattr(desks.idx, "quote",
                        lambda *a, **k: {"available": True, "last": 25000.0, "source": "test"})


# ── item 7 ─────────────────────────────────────────────────────────────────

def test_order_ticket_sizes_by_risk_and_flips_levels(quote_ok):
    buy = desks.order_ticket("NIFTY", "BUY", capital=1_000_000, risk_pct=1.0,
                             frame=_frame())
    assert buy["available"] is True
    assert buy["metrics"]["lots"] >= 1
    assert buy["levels"]["stop"] < buy["levels"]["entry"] < buy["levels"]["target"]
    assert buy["metrics"]["risk_total"] <= buy["metrics"]["risk_budget"] + 1e-6
    assert "margin" in buy["margin_note"].lower()

    sell = desks.order_ticket("NIFTY", "SELL", capital=1_000_000, risk_pct=1.0,
                              frame=_frame())
    assert sell["levels"]["target"] < sell["levels"]["entry"] < sell["levels"]["stop"]


def test_order_ticket_refuses_size_when_budget_is_below_one_lot(quote_ok):
    out = desks.order_ticket("NIFTY", "BUY", capital=1_000, risk_pct=0.1,
                             frame=_frame())
    assert out["metrics"]["lots"] == 0
    assert out["bias"] == "FLAT"
    assert out["metrics"]["risk_total"] == 0


def test_account_snapshot_reads_the_local_ledger():
    out = desks.account_snapshot(capital=250_000)
    assert out["available"] is True
    assert out["metrics"]["buying_power"] == 250_000
    assert isinstance(out["orders"], list)


# ── item 8 ─────────────────────────────────────────────────────────────────

def test_flow_tape_reads_put_writing_as_support():
    out = desks.flow_tape("NIFTY", rows=_chain(), spot=25000.0)
    assert out["available"] is True
    assert out["bias"] == "LONG"
    assert out["metrics"]["smart_net"] > 0
    assert len(out["rows"]) == len(_chain())
    assert "proxy" in out["note"]


def test_flow_tape_refuses_without_a_chain():
    assert desks.flow_tape("NIFTY", rows=[])["available"] is False


# ── item 9 ─────────────────────────────────────────────────────────────────

def test_risk_profile_breakevens_bracket_spot():
    out = desks.risk_profile("NIFTY", "long_straddle", rows=_chain(), spot=25000.0)
    assert out["available"] is True
    assert out["metrics"]["breakeven_low"] < 25000 < out["metrics"]["breakeven_high"]
    assert out["metrics"]["max_loss"] > 0
    assert out["rows"][4]["pnl"] == pytest.approx(-out["metrics"]["premium"])


def test_short_straddle_inverts_payoff_and_has_no_capped_loss():
    long_ = desks.risk_profile("NIFTY", "long_straddle", rows=_chain(), spot=25000.0)
    short = desks.risk_profile("NIFTY", "short_straddle", rows=_chain(), spot=25000.0)
    assert short["metrics"]["max_loss"] is None
    assert short["rows"][4]["pnl"] == pytest.approx(long_["metrics"]["premium"])
    assert short["bias"] == "SHORT VOL"


def test_probability_cone_bands_are_ordered(quote_ok):
    cone = desks.probability_cone("NIFTY", days=30, frame=_frame())
    assert cone["available"] is True
    levels = [b["level"] for b in cone["bands"]]
    assert levels == sorted(levels)
    assert levels[2] == pytest.approx(25000.0)
    assert levels[0] < 25000 < levels[-1]


# ── item 10 ────────────────────────────────────────────────────────────────

def test_chart_desk_computes_indicators(quote_ok):
    out = desks.chart_desk("NIFTY", frame=_frame())
    assert out["available"] is True
    assert len(out["rows"]) == 120
    assert 0 <= out["metrics"]["rsi14"] <= 100
    assert out["metrics"]["ema200"] is not None
    assert out["metrics"]["support"] <= out["metrics"]["resistance"]
    assert out["bias"] in ("LONG", "SHORT")


def test_chart_desk_refuses_short_history():
    assert desks.chart_desk("NIFTY", frame=_frame(40))["available"] is False


# ── item 19 ────────────────────────────────────────────────────────────────

def test_agent_desk_has_four_specialists_rolled_up(quote_ok):
    flow = {"available": True, "bias": "FLAT", "confidence": 0.3, "detail": "balanced"}
    out = desks.agent_desk("NIFTY", frame=_frame(), flow=flow)
    assert out["available"] is True
    assert len(out["agents"]) == 4
    assert out["consensus"]["available"] is True
    assert out["llm_prose"] in (True, False)
    assert {a["agent"] for a in out["agents"]} == {"momentum", "reversion", "flow", "risk"}


# ── item 23 ────────────────────────────────────────────────────────────────

def _replay_frame() -> pd.DataFrame:
    n = 80
    close = np.full(n, 100.0)
    open_ = np.full(n, 100.0)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    close[60], open_[60], high[60] = 120.0, 100.0, 121.0
    close[61], open_[61], high[61], low[61] = 100.0, 120.0, 140.0, 80.0
    return pd.DataFrame({
        "timestamps": pd.date_range("2025-01-01", periods=n, freq="D"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": np.full(n, 1000.0),
    })


def test_replay_books_stop_before_target_when_both_are_touched():
    out = desks.replay_frames("NIFTY", frame=_replay_frame())
    assert out["available"] is True
    assert out["metrics"]["trades"] == 1
    assert out["trades"][-1]["result"] == "stop"
    assert out["metrics"]["total_return_pct"] < 0
    assert len(out["frames"]) == out["metrics"]["bars"]


def test_replay_refuses_short_history():
    assert desks.replay_frames("NIFTY", frame=_frame(20))["available"] is False


# ── item 24 ────────────────────────────────────────────────────────────────

def test_liquidity_scan_reads_a_bid_heavy_book():
    out = desks.liquidity_scan("NIFTY", rows=_chain(bid_heavy=True))
    assert out["available"] is True
    assert out["bias"] == "LONG"
    assert out["metrics"]["net_imbalance"] > 0
    assert len(out["rows"]) <= 12


def test_liquidity_scan_refuses_without_depth():
    out = desks.liquidity_scan("NIFTY", rows=_chain(depth=False))
    assert out["available"] is False
    assert "depth" in out["reason"]
