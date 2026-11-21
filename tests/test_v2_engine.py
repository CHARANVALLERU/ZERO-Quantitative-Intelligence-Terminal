"""Offline tests for ZERO Engine v2.0.

Runs under pytest, or directly:  python tests/test_v2_engine.py
No network is required; no file outside db/v2 and strategies/ is written.
"""

from __future__ import annotations

import datetime as _dt
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.v2 import (  # noqa: E402
    aggregator,
    macro_factors,
    market_structure,
    mcp_client,
    rea_bridge,
    session_brief,
    state_audit,
    strategy_lab,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _frame(n: int = 420, seed: int = 7, drift: float = 0.0006) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, 0.008, n)
    close = 20000 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    volume = rng.integers(90_000, 160_000, n).astype(float)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": open_, "High": np.maximum.reduce([high, open_, close]),
         "Low": np.minimum.reduce([low, open_, close]), "Close": close,
         "Volume": volume},
        index=idx,
    )


# ── Macro graph ─────────────────────────────────────────────────────────────

def test_macro_graph_signs_and_cap():
    prev = macro_factors.MacroSnapshot(
        asof="2026-11-20T09:00:00", crude_usd=70.0, gold_usd=2600.0,
        dxy=100.0, usdinr=88.0, cpi_yoy_pct=5.5, repo_rate_pct=5.5,
        supply_gap_pct=0.5, nifty_cagr_pct=10.1,
    )
    cur = macro_factors.MacroSnapshot(
        asof="2026-11-21T09:00:00", crude_usd=80.0, gold_usd=2750.0,
        dxy=98.0, usdinr=88.5, cpi_yoy_pct=6.09, repo_rate_pct=5.75,
        supply_gap_pct=1.2, nifty_cagr_pct=10.1,
    )
    out = macro_factors.analyze(cur, prev)

    assert out["net_pct"]["NIFTY 50"] < 0, "crude+rate shock must hurt equities"
    for index, value in out["net_pct"].items():
        assert abs(value) <= macro_factors.MAX_TOTAL_PCT + 1e-9, index
    # Crude up while DXY down confirms rule 1 (inverse dollar).
    rule1 = next(r for r in out["rule_audit"] if r["rule"] == 1)
    assert rule1["status"] == "CONFIRMED"
    # Crude and gold both up confirms rule 4.
    rule4 = next(r for r in out["rule_audit"] if r["rule"] == 4)
    assert rule4["status"] == "CONFIRMED"
    assert out["confidence"] > 0.5
    assert out["apply"] is True


def test_macro_graph_reports_missing_inputs():
    out = macro_factors.analyze(macro_factors.MacroSnapshot(), None)
    assert out["apply"] is False
    assert "previous_snapshot" in out["missing"]
    assert out["coverage"] == 0.0


def test_macro_overlay_moves_but_never_mutates():
    matrix = {
        "NIFTY 50": {"prev_close": 24000.0, "pred_open": 24010.0,
                     "pred_high": 24150.0, "pred_low": 23860.0, "pred_close": 24040.0},
        "BANKNIFTY": {"error": "x"},
    }
    prev = macro_factors.MacroSnapshot(crude_usd=70.0, gold_usd=2600.0, dxy=100.0,
                                       usdinr=88.0, cpi_yoy_pct=5.0, repo_rate_pct=5.5,
                                       supply_gap_pct=0.0, nifty_cagr_pct=10.0)
    cur = macro_factors.MacroSnapshot(crude_usd=80.0, gold_usd=2700.0, dxy=99.0,
                                      usdinr=88.4, cpi_yoy_pct=6.0, repo_rate_pct=5.75,
                                      supply_gap_pct=1.0, nifty_cagr_pct=10.0)
    out = macro_factors.apply_macro_overlay(matrix, snapshot=cur, prev=prev)
    assert out["NIFTY 50"]["pred_open"] != matrix["NIFTY 50"]["pred_open"]
    assert matrix["NIFTY 50"]["pred_open"] == 24010.0, "input must not be mutated"
    assert out["BANKNIFTY"] == {"error": "x"}
    assert out["macro"]["apply"] is True


# ── Aggregation ─────────────────────────────────────────────────────────────

def test_yahoo_symbol_mapping():
    assert aggregator.yahoo_symbol("SBIN-EQ", "NSE") == "SBIN.NS"
    assert aggregator.yahoo_symbol("SBIN", "BSE") == "SBIN.BO"
    assert aggregator.yahoo_symbol("RELIANCE.NS", "NSE") == "RELIANCE.NS"


def test_unified_view_offline_shape():
    view = aggregator.build_unified_view(allow_network=False)
    assert view["schema"] == "2.0"
    assert isinstance(view["universe"]["nse"], list)
    assert "breadth" in view and "sources" in view


# ── Market structure ────────────────────────────────────────────────────────

def test_structure_detects_absorption_and_phase():
    df = _frame(120, seed=3)
    # Force an absorption bar: 8x volume, tiny range.
    i = -1
    prev_close = float(df["Close"].iloc[i])
    df.iloc[i, df.columns.get_loc("Open")] = prev_close
    df.iloc[i, df.columns.get_loc("Close")] = prev_close * 1.0005
    df.iloc[i, df.columns.get_loc("High")] = prev_close * 1.001
    df.iloc[i, df.columns.get_loc("Low")] = prev_close * 0.999
    df.iloc[i, df.columns.get_loc("Volume")] = float(df["Volume"].mean() * 12)

    events = market_structure.detect_events(df)
    assert any(e["type"] == "ABSORPTION" for e in events), events

    study = market_structure.study(df, "NIFTY")
    assert study["status"] == "OK"
    assert study["phase"]["phase"] in {
        "ACCUMULATION", "DISTRIBUTION", "MARKUP", "MARKDOWN", "TRANSITION"
    }
    assert study["narrative"]
    assert study["exchange_depth_available"] is False


def test_structure_handles_no_data():
    study = market_structure.study(None, "NIFTY")
    assert study["status"] == "NO_DATA"


# ── Strategy lab ────────────────────────────────────────────────────────────

def test_strategy_name_is_alphanumeric():
    name = strategy_lab.make_name("MOM", "NIFTY", {"lookback": 20})
    assert name.startswith("Z2-MOM-")
    assert name.replace("-", "").isalnum()


def test_backtest_produces_finite_metrics():
    df = _frame(500, seed=11, drift=0.0010)
    spec = strategy_lab._describe(strategy_lab.StrategySpec(
        family="MOM", index_key="NIFTY",
        params={"lookback": 20, "trend": 100, "exit_lookback": 10,
                "stop_atr": 1.5, "target_r": 2.0, "time_stop": 20},
    ))
    result = strategy_lab.backtest(df, spec)
    m = result["metrics"]
    assert m["trades"] >= 1
    for key in ("net_pnl", "profit_factor", "max_drawdown_pct", "expectancy_r"):
        assert math.isfinite(float(m[key])), key
    # Every trade must carry a stop below entry (long-only risk control).
    for trade in result["trades"]:
        assert trade["stop"] < trade["entry"]
    doc = strategy_lab.render_strategy_doc(spec, result)
    mistakes = strategy_lab.render_mistakes_doc(spec, result)
    assert spec.name in doc and "Risk:" in doc
    assert spec.name in mistakes and "P&L" in mistakes


# ── Integrity / brief / MCP ─────────────────────────────────────────────────

def test_audit_shape():
    report = state_audit.audit()
    assert 0 <= report["health_pct"] <= 100
    assert report["total"] == len(report["checks"])
    names = {c["name"] for c in report["checks"]}
    assert "python_syntax" in names and "aite_liveness" in names


def test_brief_shape():
    brief = session_brief.build()
    assert "strategies_created" in brief
    assert isinstance(session_brief.summary_line(brief), str)


def test_mcp_registry_has_rea():
    registry = mcp_client.load_registry()
    assert "rea" in registry["servers"]
    assert registry["servers"]["rea"]["command"] == "npx"


def test_rea_status_does_not_launch():
    status = rea_bridge.status()
    assert status["state"] in {"CONNECTED", "NOT_CONNECTED", "LAST_ATTEMPT_FAILED"}
    assert "environment" in status


# ── Self-runner (pytest is optional in this project) ────────────────────────

def _run_all() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
