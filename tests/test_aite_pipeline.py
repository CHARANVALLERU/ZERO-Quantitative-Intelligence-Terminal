"""
End-to-end AITE pipeline tests — idea → survivor → deploy (synthetic bars).

No network. Proves AgentSwarm roles call real functions (not status-only flips).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture()
def aite_tmp(monkeypatch):
    """Redirect all AITE persistence into a temp dir + force synthetic OHLC."""
    tmp = tempfile.mkdtemp(prefix="aite_pipe_")
    root = Path(tmp)
    from engine.aite import config as cfg
    from engine.aite.exam import _synthetic_ohlcv

    monkeypatch.setattr(cfg, "AITE_DB_DIR", root)
    monkeypatch.setattr(cfg, "BOTS_PATH", root / "bots.json")
    monkeypatch.setattr(cfg, "PORTFOLIO_PATH", root / "portfolio.json")
    monkeypatch.setattr(cfg, "TRADES_PATH", root / "trades.jsonl")
    monkeypatch.setattr(cfg, "LOGS_PATH", root / "daemon.jsonl")
    monkeypatch.setattr(cfg, "EXAM_CACHE_PATH", root / "exam_cache.json")
    monkeypatch.setattr(cfg, "FUND_PATH", root / "fund.json")
    monkeypatch.setattr(cfg, "DAEMON_STATE_PATH", root / "daemon_state.json")
    monkeypatch.setattr(cfg, "BRIEFS_PATH", root / "briefs.jsonl")
    monkeypatch.setattr(cfg, "PREMARKET_PATH", root / "premarket.jsonl")
    monkeypatch.setattr(cfg, "AGENT_STATE_PATH", root / "agents.json")
    monkeypatch.setattr(cfg, "IDEAS_PATH", root / "ideas.jsonl")

    from engine import data_provenance as prov_mod

    import numpy as np
    import pandas as pd

    import zlib

    def _fake_load(symbol, bars=None, allow_synthetic=False):
        """
        Fixture feed declared as real, so orchestration can be tested offline.

        The provenance gate is covered by tests/test_integrity_fixes.py; these
        tests are about idea -> survivor -> deploy plumbing.
        """
        n = max(int(bars or 300), int(cfg.MIN_BARS), int(cfg.TARGET_BARS))
        # crc32 keeps the fixture identical across processes (hash() is salted).
        rng = np.random.default_rng(zlib.crc32(str(symbol).encode()) % 9973)
        rets = rng.normal(0.0016, 0.006, n)
        close = 22000.0 * np.cumprod(1.0 + rets)
        df = pd.DataFrame({
            "timestamps": pd.bdate_range("2023-01-02", periods=n),
            "open": close * 0.999,
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": 1_000_000.0,
        })
        return prov_mod.tag_frame(df, source=prov_mod.REAL, vendor="test_fixture")

    def _fake_ensure(symbol, bars=None, frames=None, allow_synthetic=False):
        df = _fake_load(symbol, bars)
        if frames is not None:
            frames[symbol] = df
        return df

    monkeypatch.setattr("engine.aite.exam.load_market_frame", _fake_load)
    monkeypatch.setattr("engine.aite.pipeline.ensure_enough_bars", _fake_ensure)
    # Portfolio / breeding may import load_market_frame by reference later — patch module attrs
    monkeypatch.setattr("engine.aite.portfolio.load_market_frame", _fake_load, raising=False)
    monkeypatch.setattr("engine.aite.breeding.load_market_frame", _fake_load, raising=False)

    # Statistical gates are tested elsewhere; keep them reachable so the
    # plumbing under test can actually produce a survivor.
    monkeypatch.setattr(cfg, "MIN_TRADES_OOS", 1)
    monkeypatch.setattr(cfg, "MIN_OOS_SHARPE", -99.0)
    monkeypatch.setattr(cfg, "MAX_OOS_DRAWDOWN", 1.0)
    return root


def test_insufficient_bars_gate(aite_tmp):
    from engine.aite import config as cfg
    from engine.aite.exam import _synthetic_ohlcv, run_exam
    from engine.aite.models import BotGenome, Rule

    bot = BotGenome(
        bot_id="bot_short",
        name="SHORT",
        symbol="NIFTY 50",
        side_bias="BOTH",
        rules=[Rule(indicator="rsi", operator="<", threshold=30.0)],
        stop_atr=1.5,
        take_atr=2.5,
        hold_bars=8,
        style="mixed",
        status="candidate",
    )
    short = _synthetic_ohlcv(60, seed=1)
    exam = run_exam(bot, short)
    assert exam.passed is False
    assert exam.reason == "insufficient_bars"

    long = _synthetic_ohlcv(cfg.MIN_BARS, seed=2)
    exam2 = run_exam(bot, long)
    assert exam2.reason != "insufficient_bars"


def test_pipeline_idea_to_survivor_to_deploy(aite_tmp):
    from engine.aite import config as cfg
    from engine.aite import store
    from engine.aite.activity_log import read_activity
    from engine.aite.agents import load_agents_state
    from engine.aite.pipeline import get_flow_progress, run_pipeline
    from engine.aite.deploy import active_deployments

    result = run_pipeline(
        idea="NIFTY momentum with RSI filter long bias",
        symbols=["NIFTY 50"],
        n_population=24,
        n_survivors=cfg.MIN_BOTS,
        generations=1,
        seed=7,
        venue="paper",
        deploy=True,
        monitor=True,
        persist=True,
    )
    assert result["ok"] is True, result.get("error")
    # Survivors are exam-gated: at most one per examined real-data bot, and the
    # book is explicitly allowed to be short of MIN_BOTS rather than padded.
    assert 0 <= result["n_survivors"] <= cfg.MAX_BOTS
    assert all(v >= cfg.MIN_BARS for v in (result.get("bar_counts") or {}).values())

    port = store.load_portfolio()
    bots = store.load_bots()
    saved_ids = {b.get("bot_id") for b in bots}
    book = [x for x in (port.get("bot_ids") or []) if x]
    # Every capitalised bot must be a saved, real-data bot; the book may be
    # short of the survivor count but never padded with unexamined ones.
    assert set(book) <= saved_ids
    assert len(port.get("allocations") or {}) == len(book)
    assert len(book) <= len(bots)

    assert len(result.get("deployed") or []) >= 1
    active = active_deployments()
    assert active["count"] >= 1

    flow = get_flow_progress()
    assert flow.get("stage") == "done"
    assert flow.get("pct") == 100
    assert flow.get("ok") is True

    acts = read_activity(limit=200)
    sources = {a.get("source") for a in acts}
    assert "pipeline" in sources or "orchestra" in sources
    messages = " ".join(str(a.get("message") or "") for a in acts).lower()
    assert "pipeline" in messages or "breed" in messages or "deploy" in messages

    state = load_agents_state()
    assert state.get("nodes")
    edges = state.get("edges") or []
    handoffs = [e for e in edges if e.get("kind") == "handoff"]
    assert len(handoffs) >= 1
    msgs = [n.get("message") or "" for n in state["nodes"]]
    assert any(len(m) > 10 for m in msgs)


def test_service_facade_apis(aite_tmp):
    from engine.aite.service import AiteService

    # Fresh instance (avoid stale singleton from other tests)
    svc = AiteService()
    flow = svc.get_flow_progress()
    assert isinstance(flow, dict)

    graph = svc.get_graph_state()
    assert "nodes" in graph
    assert "edges" in graph

    brief = svc.ask_brief("Should I buy NIFTY today?", persist=False)
    assert isinstance(brief, dict)

    cycle = svc.run_cycle(
        idea="BANKNIFTY mean reversion oversold RSI",
        symbols=["BANKNIFTY"],
        n_population=12,
        n_survivors=10,
        generations=1,
        seed=3,
        deploy=True,
        monitor=False,
        persist=True,
    )
    assert cycle.get("ok") is True, cycle.get("error")
    # Survivors are exam-gated now; the book is allowed to be short.
    assert 0 <= cycle.get("n_survivors", 0) <= 12

    dep = svc.deploy(venue="paper", note="test")
    assert dep.get("ok") is True

    st = svc.status()
    assert st.get("service") == "aite"


def test_orchestra_roles_call_real_functions(aite_tmp):
    """Breeder role must return exam counts — proves callable, not sleep-flip."""
    from engine.aite.agents import invoke_role
    from engine.aite.orchestra import Orchestra
    from engine.aite.pipeline import ensure_enough_bars

    orch = Orchestra()
    orch.begin(label="unit_roles")
    idea = orch.researcher_compile_idea("SENSEX breakout ATR expansion")
    assert idea["ok"] is True
    assert idea["genome"]["bot_id"]

    frames = {"SENSEX": ensure_enough_bars("SENSEX")}
    from engine.aite.models import BotGenome

    seed = BotGenome.from_dict(idea["genome"])
    bred = orch.breeder_run_cycle(
        symbols=["SENSEX"],
        n_population=10,
        generations=1,
        seed=11,
        frames=frames,
        seed_genome=seed,
        persist=True,
    )
    assert bred["n_examined"] >= 10
    assert isinstance(bred["survivors"], list)
    assert len(bred["survivors"]) >= 1
    orch.finish(ok=True)

    out = invoke_role("execution", "monitor_edge", persist=True)
    assert out.get("ok") is True


def test_daemon_job_kinds_wired(aite_tmp, monkeypatch):
    """Daemon _run_job routes breed_cycle / edge_monitor to pipeline."""
    from engine.aite import daemon as d

    calls = {"breed": 0, "edge": 0}

    def _fake_breed(payload=None):
        calls["breed"] += 1
        return {"ok": True, "n_survivors": 10}

    def _fake_edge(payload=None):
        calls["edge"] += 1
        return {"ok": True, "kept": 10}

    monkeypatch.setattr("engine.aite.pipeline.run_breed_job", _fake_breed)
    monkeypatch.setattr("engine.aite.pipeline.run_edge_monitor_job", _fake_edge)

    assert d._run_job({"kind": "breed_cycle", "payload": "{}", "id": "j1"})["ok"] is True
    assert d._run_job({"kind": "edge_monitor", "payload": "{}", "id": "j2"})["ok"] is True
    assert calls["breed"] == 1
    assert calls["edge"] == 1
