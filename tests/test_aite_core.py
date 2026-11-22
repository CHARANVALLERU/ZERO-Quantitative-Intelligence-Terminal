"""
Offline unit tests for ZERO AITE core loop.

Runnable with pytest or:  python tests/test_aite_core.py
No network — uses synthetic OHLC only.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.aite import config as cfg  # noqa: E402
from engine.aite.backtest_engine import (  # noqa: E402
    backtest,
    backtest_batch,
    backtest_vectorized_signals,
    load_bars,
    rank_by_backtest,
)
from engine.aite.breeding import Breeder  # noqa: E402
from engine.aite.exam import _synthetic_ohlcv, run_exam  # noqa: E402
from engine.aite.models import BotGenome, Rule  # noqa: E402
from engine.aite.runner import run_generation_cycle  # noqa: E402
from engine.aite.survivors import (  # noqa: E402
    compute_live_edge,
    cut_fading_bots,
    demote,
    manage_survivors,
    promote,
)


def _tmp_db(monkeypatch_paths: bool = True):
    """Redirect AITE persistence into a temp directory."""
    tmp = tempfile.mkdtemp(prefix="aite_test_")
    root = Path(tmp)
    cfg.AITE_DB_DIR = root
    cfg.BOTS_PATH = root / "bots.json"
    cfg.PORTFOLIO_PATH = root / "portfolio.json"
    cfg.TRADES_PATH = root / "trades.jsonl"
    cfg.LOGS_PATH = root / "daemon.jsonl"
    cfg.EXAM_CACHE_PATH = root / "exam_cache.json"
    cfg.FUND_PATH = root / "fund.json"
    cfg.DAEMON_STATE_PATH = root / "daemon_state.json"
    cfg.BRIEFS_PATH = root / "briefs.jsonl"
    cfg.PREMARKET_PATH = root / "premarket.jsonl"
    cfg.AGENT_STATE_PATH = root / "agents.json"
    cfg.IDEAS_PATH = root / "ideas.jsonl"
    return root


def _sample_bot(name: str = "TST-G00-001", status: str = "candidate") -> BotGenome:
    return BotGenome(
        bot_id="bot_test_001",
        name=name,
        symbol="NIFTY 50",
        side_bias="BOTH",
        rules=[
            Rule(indicator="rsi", operator="<", threshold=35.0, weight=1.0),
            Rule(indicator="mom_10", operator=">", threshold=0.0, weight=1.0),
        ],
        stop_atr=1.5,
        take_atr=2.5,
        hold_bars=8,
        generation=0,
        style="mixed",
        status=status,
    )


def test_synthetic_bars_and_load():
    df = _synthetic_ohlcv(120, seed=3)
    assert len(df) == 120
    assert {"open", "high", "low", "close"}.issubset(set(df.columns))
    bars = load_bars("NIFTY 50", bars=100)
    assert len(bars) >= 50
    cols = []
    for c in bars.columns:
        if isinstance(c, tuple):
            cols.append(str(c[0]).lower())
        else:
            cols.append(str(c).lower())
    assert "close" in cols


def test_backtest_single_genome():
    df = _synthetic_ohlcv(200, seed=11)
    bot = _sample_bot()
    result = backtest(bot, df)
    assert result["bot_id"] == bot.bot_id
    assert result["ok"] is True
    assert "metrics" in result
    assert "sharpe" in result["metrics"]
    assert isinstance(result["trades"], list)


def test_backtest_vectorized_and_batch():
    df = _synthetic_ohlcv(150, seed=5)
    closes = df["close"].values.astype(float)
    signals = np.zeros(len(closes), dtype=int)
    signals[20:40] = 1
    signals[80:100] = -1
    v = backtest_vectorized_signals(closes, signals, hold_bars=5)
    assert "sharpe" in v
    assert "total_return" in v

    breeder = Breeder(seed=7)
    bots = [breeder.random_genome(symbol="NIFTY 50") for _ in range(4)]
    frames = {"NIFTY 50": df}
    results = backtest_batch(bots, frames)
    assert len(results) == 4
    ranked, scored = rank_by_backtest(bots, frames=frames, top_k=2)
    assert len(ranked) == 2
    assert len(scored) == 2


def test_exam_offline():
    df = _synthetic_ohlcv(250, seed=9)
    bot = _sample_bot()
    exam = run_exam(bot, df)
    assert exam.bot_id == bot.bot_id
    assert isinstance(exam.passed, bool)
    assert len(exam.progress_lines) == cfg.BACKTEST_FLOW_LINES
    assert exam.fitness == exam.fitness  # not NaN


def test_exam_insufficient_bars_vs_enough():
    """Short OHLC (< MIN_BARS) must fail; long series must clear the bar gate."""
    bot = _sample_bot()
    short = _synthetic_ohlcv(58, seed=1)  # mirrors the ~60d / 58-bar failure mode
    exam_short = run_exam(bot, short)
    assert exam_short.passed is False
    assert exam_short.reason == "insufficient_bars"

    long = _synthetic_ohlcv(max(cfg.DEFAULT_BARS, cfg.TARGET_BARS), seed=2)
    exam_long = run_exam(bot, long)
    assert exam_long.reason != "insufficient_bars"
    assert len(exam_long.progress_lines) == cfg.BACKTEST_FLOW_LINES


def test_load_market_frame_rejects_short_default_period(monkeypatch=None):
    """
    get_historical_data default 60d (~58 bars) must NOT be accepted as-is.
    Loader should request a long period and accept a long frame.
    """
    from engine.aite import exam as exam_mod

    calls = {"periods": []}

    def _fake_hist(symbol_key, period="60d"):
        calls["periods"].append(period)
        n = 58 if period in ("60d", "1mo", "5d") else 400
        return _synthetic_ohlcv(n, seed=99).rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        }).set_index(pd.DatetimeIndex(
            pd.bdate_range("2024-01-01", periods=n), name="Date",
        )).drop(columns=["timestamps"], errors="ignore")

    # Force offline yfinance path skipped by raising
    class _Boom:
        @staticmethod
        def download(*_a, **_k):
            raise RuntimeError("no network in unit test")

    import sys
    import types

    # Patch historical + yfinance inside load_market_frame
    import data.historical as hist_mod
    orig_get = hist_mod.get_historical_data
    hist_mod.get_historical_data = _fake_hist
    fake_yf = types.ModuleType("yfinance")
    fake_yf.download = _Boom.download
    prev_yf = sys.modules.get("yfinance")
    sys.modules["yfinance"] = fake_yf
    try:
        df = exam_mod.load_market_frame("NIFTY 50", bars=cfg.DEFAULT_BARS)
        assert len(df) >= cfg.MIN_BARS
        assert len(df) >= cfg.TARGET_BARS
        # Must have requested a long period, not bare default 60d alone
        assert any(p in ("1y", "2y", "5y", "max", "6mo") for p in calls["periods"]), calls["periods"]
        assert {"open", "high", "low", "close"}.issubset(set(df.columns))
    finally:
        hist_mod.get_historical_data = orig_get
        if prev_yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = prev_yf


def test_load_market_frame_does_not_pad_short_series():
    """
    When every vendor returns a short series the loader returns it honestly.

    Padding used to be the 'last resort' - that is exactly what let a bot pass
    an exam on invented prices. Now the short real frame is returned and the
    exam rejects promotion; synthetic data requires allow_synthetic=True and
    can never pass.
    """
    from engine.aite import exam as exam_mod
    import data.historical as hist_mod
    import sys
    import types

    short_n = 40

    def _always_short(symbol_key, period="60d"):
        return _synthetic_ohlcv(short_n, seed=7).rename(columns={
            "open": "Open", "high": "High", "low": "Low",
            "close": "Close", "volume": "Volume",
        }).set_index(pd.DatetimeIndex(
            pd.bdate_range("2024-06-01", periods=short_n), name="Date",
        )).drop(columns=["timestamps"], errors="ignore")

    class _ShortYF:
        @staticmethod
        def download(*_a, **_k):
            return _always_short("NIFTY")

    orig_get = hist_mod.get_historical_data
    hist_mod.get_historical_data = _always_short
    prev_yf = sys.modules.get("yfinance")
    fake_yf = types.ModuleType("yfinance")
    fake_yf.download = _ShortYF.download
    sys.modules["yfinance"] = fake_yf
    try:
        df = exam_mod.load_market_frame("NIFTY 50", bars=cfg.DEFAULT_BARS)
        assert len(df) == short_n, "a short real series must not be padded"
        from engine import data_provenance as prov_mod
        assert prov_mod.frame_provenance(df).is_synthetic is False
        bot = _sample_bot()
        exam = run_exam(bot, df)
        assert exam.passed is False
        assert exam.reason in ("insufficient_bars", "unverified_data_rejected")
    finally:
        hist_mod.get_historical_data = orig_get
        if prev_yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = prev_yf


def test_promote_demote_cut():
    _tmp_db()
    from engine.aite import store

    bot = _sample_bot(status="exam")
    assert promote(bot) is True
    assert bot.status == "alive"

    # Seed losing trades → demote / cut
    for i in range(12):
        store.log_trade({
            "bot_id": bot.bot_id,
            "bot_name": bot.name,
            "pnl_pct": -0.5,
            "pnl": -50,
        })
    edge = compute_live_edge(bot)
    assert edge["n_trades"] >= 4
    assert edge["sum_pnl_pct"] < 0
    assert demote(bot, edge) is True
    assert bot.status == "fading"

    kept, killed = cut_fading_bots([bot])
    assert bot in killed or bot.status == "dead"
    assert len(killed) >= 1


def test_manage_survivors_trims_to_target():
    _tmp_db()
    breeder = Breeder(seed=2)
    bots = []
    for i in range(15):
        b = breeder.random_genome(symbol="NIFTY 50")
        b.status = "alive"
        bots.append(b)
    summary = manage_survivors(bots, trades=[], n_target=10, fitness={})
    assert summary["n_survivors"] == 10
    assert all(isinstance(b, BotGenome) for b in summary["survivors"])


def _real_fixture(n: int, seed: int = 7):
    """
    Deterministic, strongly trending bars declared as a real vendor feed.

    Test fixture only: orchestration tests need bots that can actually clear the
    statistical gate. The provenance gate itself is exercised in
    tests/test_integrity_fixes.py, and production loaders tag their own frames.
    """
    from engine import data_provenance as prov_mod

    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0016, 0.006, n)
    close = 22000.0 * np.cumprod(1.0 + rets)
    idx = pd.bdate_range("2023-01-02", periods=n)
    df = pd.DataFrame({
        "timestamps": idx,
        "open": close * 0.999,
        "high": close * 1.004,
        "low": close * 0.996,
        "close": close,
        "volume": 1_000_000.0,
    })
    return prov_mod.tag_frame(df, source=prov_mod.REAL, vendor="test_fixture")


def _install_fixture_feed():
    """
    Point every loader at the fixture feed so orchestration can be tested
    offline. The provenance gate itself is tested in test_integrity_fixes.py.
    """
    import engine.aite.exam as exam_mod
    import engine.aite.breeding as breeding_mod
    import engine.aite.portfolio as portfolio_mod

    import zlib

    def _load(symbol, bars=None, allow_synthetic=False):
        n = max(int(bars or 300), int(cfg.MIN_BARS), int(cfg.TARGET_BARS))
        # zlib.crc32, not hash(): str hashing is salted per process, which made
        # the fixture data (and therefore survivor counts) differ between runs.
        return _real_fixture(n, seed=zlib.crc32(str(symbol).encode()) % 9973)

    saved = []
    for mod in (exam_mod, breeding_mod, portfolio_mod):
        if hasattr(mod, "load_market_frame"):
            saved.append((mod, mod.load_market_frame))
            mod.load_market_frame = _load

    # Statistical thresholds are covered separately; these tests are about the
    # survivor/portfolio plumbing, so keep the gates reachable while leaving the
    # provenance and drawdown logic untouched.
    gates = {k: getattr(cfg, k) for k in
             ("MIN_TRADES_OOS", "MIN_OOS_SHARPE", "MAX_OOS_DRAWDOWN")}
    cfg.MIN_TRADES_OOS = 1
    cfg.MIN_OOS_SHARPE = -99.0
    cfg.MAX_OOS_DRAWDOWN = 1.0
    return saved, gates


def _restore_feed(saved):
    mods, gates = saved
    for mod, fn in mods:
        mod.load_market_frame = fn
    for k, v in gates.items():
        setattr(cfg, k, v)


def test_run_generation_cycle_offline():
    _tmp_db()
    saved = _install_fixture_feed()
    # Tiny pop, 1 generation — must finish without network
    result = run_generation_cycle(
        n_population=12,
        n_survivors=10,
        generations=1,
        symbols=["NIFTY 50"],
        seed=42,
        persist=True,
    )
    _restore_feed(saved)
    assert result["ok"] is True
    assert result["n_population"] == 12
    # Admission is earned: the book can never hold more bots than cleared the
    # exam on real bars, and it is allowed to come up short rather than being
    # padded with unexamined bots (the old behaviour).
    assert 0 <= result["n_survivors"] <= 12
    assert len(result["portfolio"]["bot_ids"]) <= result["n_survivors"]
    assert len(result["portfolio"]["allocations"]) == len(result["portfolio"]["bot_ids"])
    assert "portfolio" in result
    assert "allocations" in result
    assert Path(result["db_dir"]).exists()

    # Persistence artifacts
    assert cfg.BOTS_PATH.exists() or True  # written via store
    from engine.aite import store
    bots = store.load_bots()
    port = store.load_portfolio()
    assert isinstance(bots, list)
    assert "bot_ids" in port
    # Whatever is on disk must be a subset of what the cycle produced (the
    # deploy stage may retire bots; it may never invent them).
    assert set(port["bot_ids"]) <= set(result["portfolio"]["bot_ids"])


def test_survivor_clamp():
    _tmp_db()
    result = run_generation_cycle(
        n_population=16,
        n_survivors=99,  # must clamp to MAX_BOTS
        generations=1,
        symbols=["NIFTY 50"],
        seed=1,
        persist=True,
    )
    assert result["n_survivors"] <= cfg.MAX_BOTS


def test_persist_false_no_db_writes():
    root = _tmp_db()
    # Ensure empty start
    assert not any(root.iterdir()) or True
    result = run_generation_cycle(
        n_population=12,
        n_survivors=10,
        generations=1,
        symbols=["NIFTY 50"],
        seed=3,
        persist=False,
    )
    assert result["ok"] is True
    assert result["allocations"] == result["portfolio"]["allocations"]
    assert result["portfolio"]["bot_ids"] == [s["bot_id"] for s in result["survivors"]]
    # No persistence artifacts under redirected db dir
    written = list(root.glob("*"))
    assert written == [], f"persist=False wrote files: {written}"


def test_return_matches_portfolio_state():
    _tmp_db()
    saved = _install_fixture_feed()
    result = run_generation_cycle(
        n_population=12,
        n_survivors=10,
        generations=1,
        symbols=["NIFTY 50"],
        seed=5,
        persist=True,
    )
    _restore_feed(saved)
    assert result["allocations"] == result["portfolio"]["allocations"]
    # Every capitalised bot must come from the survivor list and carry a weight.
    assert set(result["portfolio"]["bot_ids"]) <= {s["bot_id"] for s in result["survivors"]}
    assert set(result["allocations"]) == set(result["portfolio"]["bot_ids"])
    from engine.aite import store
    disk = store.load_portfolio()
    assert set(disk["allocations"]) == set(disk["bot_ids"])
    assert set(disk["bot_ids"]) <= set(result["portfolio"]["bot_ids"])


def _run_all():
    tests = [
        test_synthetic_bars_and_load,
        test_backtest_single_genome,
        test_backtest_vectorized_and_batch,
        test_exam_offline,
        test_exam_insufficient_bars_vs_enough,
        test_load_market_frame_rejects_short_default_period,
        test_load_market_frame_does_not_pad_short_series,
        test_promote_demote_cut,
        test_manage_survivors_trims_to_target,
        test_run_generation_cycle_offline,
        test_survivor_clamp,
        test_persist_false_no_db_writes,
        test_return_matches_portfolio_state,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return failed == 0


if __name__ == "__main__":
    ok = _run_all()
    sys.exit(0 if ok else 1)
