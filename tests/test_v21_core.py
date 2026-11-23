"""v2.1 engine tests — no network, no Streamlit, deterministic."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine.v21 import ecosystem, forecast, gex, indices, integrations, quant, reports, signals


# ── indices ────────────────────────────────────────────────────────────────


def test_index_aliases_resolve():
    assert indices.canonical("NIFTY 50") == "NIFTY"
    assert indices.canonical("^NSEI") == "NIFTY"
    assert indices.canonical("bank nifty") == "BANKNIFTY"
    assert indices.canonical("^BSESN") == "SENSEX"
    assert indices.index_options() == ["NIFTY 50", "SENSEX", "BANKNIFTY"]


def test_quote_never_raises_on_unknown_index():
    q = indices.quote("does-not-exist")
    assert set(q) >= {"available", "index", "reason"}


# ── GEX ────────────────────────────────────────────────────────────────────


def _chain(spot=25000.0, expiry="27-Nov-2026", strikes=(24800, 25000, 25200)):
    rows = []
    for k in strikes:
        rows.append({
            "strikePrice": k,
            "expiry": expiry,
            "CE": {"openInterest": 100000, "impliedVolatility": 14.0,
                   "changeinOpenInterest": 1000, "lastPrice": 50.0},
            "PE": {"openInterest": 60000, "impliedVolatility": 15.0,
                   "changeinOpenInterest": 500, "lastPrice": 40.0},
        })
    return {"records": {"underlyingValue": spot, "expiryDates": [expiry], "data": rows},
            "filtered": {"data": rows}}


def test_gamma_peaks_at_the_money():
    atm = gex.bs_gamma(25000, 25000, 0.14, 7 / 365)
    otm = gex.bs_gamma(25000, 26000, 0.14, 7 / 365)
    assert atm > otm > 0


def test_gex_profile_and_regime():
    out = gex.compute_gex(_chain())
    assert out["available"] is True
    assert len(out["profile"]) == 3
    assert out["totals"]["net_gex"] != 0
    assert out["regime"] in ("positive", "negative")
    # Calls carry more OI than puts in the fixture, so the book is long gamma.
    assert out["regime"] == "positive"
    assert out["suggestion"]["label"] == "GEX positive"
    assert out["gamma_flip"] is not None or out["totals"]["call_wall"] > 0


def test_gex_rejects_empty_payload():
    assert gex.compute_gex({})["available"] is False
    assert gex.compute_gex({"records": {"underlyingValue": 0, "data": []}})["available"] is False


# ── signals ────────────────────────────────────────────────────────────────


def test_hedge_regression_recovers_beta():
    rng = np.random.default_rng(2)
    m = rng.normal(0, 0.01, 500)
    s = 1.5 * m + rng.normal(0, 0.002, 500)
    out = signals.hedge_regression(m, s, capital=1_000_000)
    assert out["available"] is True
    assert abs(out["metrics"]["beta"] - 1.5) < 0.05
    assert out["metrics"]["r2"] > 0.9
    assert out["metrics"]["swing_reduction_pct"] > 50


def test_hedge_regression_needs_history():
    assert signals.hedge_regression([0.01] * 5, [0.01] * 5)["available"] is False


def test_stat_arb_enters_and_exits():
    rng = np.random.default_rng(4)
    b = np.cumsum(rng.normal(0, 1, 700)) + 100
    a = 1.2 * b + rng.normal(0, 2.0, 700)
    out = signals.stat_arb(a, b, lookback=250)
    assert out["available"] is True
    assert out["trades"], "a z-score pairs engine must close at least one trade"
    assert all(t["exit_bar"] > t["entry_bar"] for t in out["trades"])
    assert out["metrics"]["hit_rate_pct"] is not None


def test_pattern_detects_breakout_on_expansion():
    n = 120
    close = np.linspace(100, 110, n)
    df = pd.DataFrame({
        "open": close * 0.999, "high": close * 1.004, "low": close * 0.996,
        "close": close, "volume": np.full(n, 1000.0),
    })
    df.loc[n - 1, ["open", "high", "low", "close", "volume"]] = [110, 118, 109, 117.5, 4000]
    out = signals.chart_context(df)
    assert out["available"] is True
    assert out["pattern"] == "breakout"
    assert "accepting" in out["behaviour"]


def test_pattern_detects_sweep():
    n = 120
    close = np.linspace(100, 110, n)
    df = pd.DataFrame({
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 1000.0),
    })
    # Wick above the range, close back inside, small body.
    df.loc[n - 1, ["open", "high", "low", "close"]] = [109.8, 120, 109.5, 109.6]
    out = signals.chart_context(df)
    assert out["pattern"] == "liquidity_sweep"
    assert "rejecting" in out["behaviour"]


def test_consensus_unanimous_and_tie():
    long_ideas = [
        {"engine": "gex", "label": "GEX positive", "bias": "BUY", "confidence": 0.6, "available": True},
        {"engine": "hedge", "label": "h", "bias": "LONG", "confidence": 0.7, "available": True},
        {"engine": "pattern", "label": "p", "bias": "LONG SPREAD", "confidence": 0.5, "available": True},
    ]
    out = signals.consensus(long_ideas)
    assert out["available"] is True
    assert out["bias"] == "LONG"
    assert out["action"] == "BUY"
    assert out["longs"] == 3 and out["shorts"] == 0
    assert out["label"].startswith("CONSENSUS LONG")
    assert out["gex_label"] == "GEX positive"

    # 1 long, 1 short, 1 flat -> no majority.
    tie = signals.consensus(long_ideas[:1] + [
        {"engine": "statarb", "label": "s", "bias": "SHORT", "confidence": 0.6, "available": True},
        {"engine": "hedge", "label": "h", "bias": "BALANCED", "confidence": 0.6, "available": True}])
    assert tie["bias"] == "MIXED"
    assert tie["action"] == "WAIT"
    assert "MIXED" in tie["label"]

    # One engine is not a consensus.
    assert signals.consensus(long_ideas[:1])["available"] is False


def test_macro_priority_gold_only_on_big_swings():
    small = signals.macro_priority(0.4, 1.0, 7.10, 7.05, 4.5)
    big = signals.macro_priority(2.6, 3.5, 7.40, 7.20, 4.5)
    gold_small = next(x for x in small if x["asset"] == "GOLD")
    gold_big = next(x for x in big if x["asset"] == "GOLD")
    assert gold_small["priority"] == "low"
    assert gold_big["priority"] == "TOP"
    y = next(x for x in big if x["asset"] == "BOND_YIELD_10Y")
    assert "inflation" in y["message"].lower()
    assert y["real_yield"] == pytest.approx(2.9, abs=0.01)


# ── quant ──────────────────────────────────────────────────────────────────


def test_bond_prices_at_par():
    price = quant.bond_price(1000, 0.07, 10, 0.07)
    assert price == pytest.approx(1000.0, abs=1e-6)
    d = quant.bond_duration(1000, 0.07, 10, 0.07)
    assert 6.5 < d["macaulay"] < 8.5
    assert d["modified"] < d["macaulay"]


def test_black_scholes_delta_bounds():
    call = quant.black_scholes(25000, 25000, 30 / 365, 0.14)
    put = quant.black_scholes(25000, 25000, 30 / 365, 0.14, kind="put")
    assert 0.4 < call["delta"] < 0.65
    assert call["delta"] - put["delta"] == pytest.approx(1.0, abs=1e-6)
    assert call["price"] > 0 and put["price"] > 0


def test_convex_rebalance_weights_are_valid():
    cov = [[0.04, 0.01, 0.005], [0.01, 0.02, 0.004], [0.005, 0.004, 0.03]]
    out = quant.convex_rebalance(cov, mu=[0.12, 0.08, 0.06], max_weight=0.6)
    assert out["available"] is True
    assert abs(sum(out["weights"]) - 1.0) < 1e-6
    assert max(out["weights"]) <= 0.6 + 1e-9
    assert out["vol"] > 0


def test_pca_single_factor_recovers_structure():
    rng = np.random.default_rng(9)
    f = rng.normal(0, 1, 400)
    R = np.vstack([f * 1.0 + rng.normal(0, 0.1, 400),
                   f * 0.9 + rng.normal(0, 0.1, 400),
                   f * 1.1 + rng.normal(0, 0.1, 400)])
    out = quant.pca_factors(R, n_components=2)
    assert out["available"] is True
    assert out["explained_variance"][0] > 0.85


# ── integrations ───────────────────────────────────────────────────────────


def test_every_requested_item_is_registered():
    items = {r["item"] for r in integrations.all_integrations()}
    missing = [n for n in range(1, 25) if n not in items]
    assert not missing, f"items missing from the registry: {missing}"


def test_registry_statuses_are_in_vocabulary():
    for row in integrations.all_integrations():
        assert row["status"] in integrations.STATUSES, row["id"]


def test_every_run_action_resolves():
    bad = []
    for row in integrations.all_integrations():
        if row["action"].startswith("run:"):
            res = integrations.resolve_action(row["action"])
            if not res["ok"]:
                bad.append((row["id"], res.get("reason")))
    assert not bad, bad


def test_external_items_are_honest():
    # A REA clone must never be reported as live.
    for row in integrations.all_integrations():
        if row["action"].startswith("rea://"):
            assert row["status"] == "external"


# ── forecast / reports / ecosystem ─────────────────────────────────────────


def test_all_six_models_are_registered():
    keys = {m["key"] for m in forecast.status()}
    assert keys == {"kronos", "fincast", "chronos2", "timesfm", "tirex2", "moirai"}


def test_uninstalled_model_refuses_to_forecast():
    res = forecast.run("fincast", "NIFTY")
    assert res["available"] is False
    assert "not installed" in res["reason"]


def test_report_renderer_marks_unavailable_sections():
    md = reports._render("NIFTY", "20260101_000000", {"quote": {"available": False, "reason": "offline"}})
    assert "unavailable: offline" in md
    assert "no value is estimated" in md


def test_ecosystem_levels_are_sane():
    long_lv = ecosystem._levels(100.0, 2.0, "LONG")
    short_lv = ecosystem._levels(100.0, 2.0, "SHORT")
    assert long_lv["stop"] < long_lv["entry"] < long_lv["target"]
    assert short_lv["target"] < short_lv["entry"] < short_lv["stop"]
