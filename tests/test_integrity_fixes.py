"""
Integrity regression tests for the 2026-11-22 hardening pass.

These cover the three defects that could silently manufacture an edge:

1. synthetic OHLCV feeding the AITE exam / promotion path;
2. per-trade P&L "correlation" that was not time-aligned;
3. an unauthenticated backend API with a hard-coded signing key.

Plus the additions that exist to stop the same class of error returning:
calendar-aligned book risk, PBO/DSR, the point-in-time store, the order state
machine and the real cost model.

Run with:  python -m pytest tests/test_integrity_fixes.py -q
       or: python tests/test_integrity_fixes.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine import data_provenance as prov  # noqa: E402
from engine.aite import exam as exam_mod  # noqa: E402
from engine.aite.models import BotGenome, Rule  # noqa: E402
from engine.aite import portfolio as aite_portfolio  # noqa: E402


def _bot(name: str = "TST", rules=None) -> BotGenome:
    return BotGenome(
        bot_id=f"bot_{name}",
        name=name,
        symbol="NIFTY 50",
        side_bias="BOTH",
        rules=rules or [Rule(indicator="rsi", operator="<", threshold=35.0)],
        stop_atr=1.5,
        take_atr=2.5,
        hold_bars=8,
        style="mixed",
        status="candidate",
    )


def _real_frame(n: int = 400, seed: int = 7, drift: float = 0.0006, vol: float = 0.008):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n)
    rets = rng.normal(drift, vol, n)
    close = 22000.0 * np.cumprod(1.0 + rets)
    df = pd.DataFrame({
        "timestamps": idx,
        "open": close * 0.999,
        "high": close * 1.004,
        "low": close * 0.996,
        "close": close,
        "volume": np.full(n, 1_000_000.0),
    })
    return prov.tag_frame(df, source=prov.REAL, vendor="test_feed", symbol="NIFTY 50")


# ── 1. Synthetic bars ───────────────────────────────────────────────────────

def test_synthetic_frame_is_tagged_and_rejected():
    df = exam_mod._synthetic_ohlcv(400, seed=3)
    p = prov.frame_provenance(df)
    assert p.source == prov.SYNTHETIC
    assert p.is_synthetic

    result = exam_mod.run_exam(_bot(), df)
    assert result.passed is False
    assert result.reason == "synthetic_data_rejected"
    assert result.data_source == prov.SYNTHETIC
    # metrics are still produced for the UI, they just cannot promote a bot
    assert result.progress_lines


def test_real_frame_passes_and_records_provenance():
    df = _real_frame()
    result = exam_mod.run_exam(_bot(), df)
    assert result.data_source == prov.REAL
    assert result.data_provenance["rows"] == len(df)
    assert result.reason in ("PASS",) or result.reason.startswith("FAIL")


def test_untagged_frame_is_unverified_not_real():
    df = _real_frame()
    df.attrs.pop("provenance", None)
    p = prov.frame_provenance(df)
    # A frame with no explicit claim is never treated as real.
    assert p.source == prov.UNVERIFIED
    assert exam_mod.run_exam(_bot(), df).passed is False


def test_loader_does_not_pad_short_series(monkeypatch):
    short = _real_frame(60, seed=5)
    monkeypatch.setattr(exam_mod, "cfg", exam_mod.cfg)
    monkeypatch.setattr("data.historical.get_historical_data",
                        lambda *_a, **_k: short)
    import types
    fake_yf = types.ModuleType("yfinance")
    fake_yf.download = lambda *_a, **_k: short
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    frame = exam_mod.load_market_frame("NIFTY 50", bars=400)
    assert len(frame) == 60, "a short real series must be returned unpadded"
    assert prov.frame_provenance(frame).is_synthetic is False


def test_predemotion_gate_helper():
    ok, reason = prov.is_promotable(prov.Provenance(source=prov.MIXED, rows=500), 252)
    assert not ok and reason == "synthetic_data_rejected"
    ok, reason = prov.is_promotable(prov.Provenance(source=prov.REAL, rows=500), 252)
    assert ok and reason == "ok"
    ok, reason = prov.is_promotable(prov.Provenance(source=prov.REAL, rows=10), 252)
    assert not ok and reason == "insufficient_bars"


# ── 2. Time-aligned returns ─────────────────────────────────────────────────

def _series(mapping):
    return pd.Series(list(mapping.values()), index=pd.to_datetime(list(mapping.keys())))


def test_correlation_requires_shared_calendar():
    a = _series({"2024-01-01": 0.01, "2024-01-02": -0.02, "2024-01-03": 0.03})
    b = _series({"2024-06-01": 0.01, "2024-06-02": -0.02, "2024-06-03": 0.03})
    corr, overlap = aite_portfolio.pairwise_corr(a, b, min_overlap=3)
    assert corr is None and overlap == 0


def test_correlation_uses_overlapping_dates_only():
    idx = pd.bdate_range("2024-01-01", periods=40)
    rng = np.random.default_rng(11)
    x = pd.Series(rng.normal(0, 0.01, 40), index=idx)
    # same path, but shifted onto different dates for the first half
    other_idx = pd.bdate_range("2024-06-01", periods=40)
    y = pd.Series(x.values, index=other_idx)
    corr, overlap = aite_portfolio.pairwise_corr(x, y, min_overlap=20)
    assert corr is None, "disjoint calendars must not produce a correlation"

    y2 = pd.Series(x.values * 2.0, index=idx)
    corr2, overlap2 = aite_portfolio.pairwise_corr(x, y2, min_overlap=20)
    assert overlap2 == 40 and corr2 == pytest.approx(1.0, abs=1e-6)


def test_trade_daily_series_books_returns_on_exit_date():
    trades = [
        {"exit_time": "2024-03-01 15:30:00", "pnl_pct": 2.0},
        {"exit_time": "2024-03-01 15:30:00", "pnl_pct": -1.0},
        {"exit_time": "2024-03-04T15:30:00", "pnl_pct": 0.5},
    ]
    s = aite_portfolio._trades_to_daily(trades)
    assert len(s) == 2
    assert s.iloc[0] == pytest.approx(0.01)  # 2% - 1% on the same day


def test_placeholder_timestamps_cannot_be_aligned():
    trades = [{"exit_time": "bar_12", "pnl_pct": 1.0},
              {"exit_time": "bar_20", "pnl_pct": 2.0}]
    s = aite_portfolio._trades_to_daily(trades)
    assert len(s) == 0


def test_portfolio_admission_requires_passing_exam(monkeypatch):
    """A bot whose exam did not pass must never be padded into the book."""
    class FakeExam:
        def __init__(self, passed):
            self.passed = passed
            self.fitness = 1.0
            self.data_provenance = prov.Provenance(source=prov.REAL, rows=400).as_dict()
            self.trades = [{"exit_time": f"2024-0{i}-01", "pnl_pct": 1.0} for i in range(1, 6)]

    bots = [_bot("good"), _bot("bad")]
    exams = {"bot_good": FakeExam(True), "bot_bad": FakeExam(False)}
    monkeypatch.setattr(aite_portfolio, "load_market_frame", lambda *_a, **_k: None)
    diag = {}
    selected, corr, rejected = aite_portfolio.select_portfolio(
        bots, exams=exams, target=10, diagnostics=diag
    )
    ids = {b.bot_id for b in selected}
    assert "bot_good" in ids
    assert "bot_bad" not in ids, "an unexamined/unpassed bot was admitted"
    assert diag["rejected_no_exam"] == 1
    assert diag["shortfall"] >= 1


# ── 3. Auth ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def auth():
    os.environ.setdefault("ZERO_SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
    from backend import auth as auth_mod
    return auth_mod


def test_refresh_token_cannot_be_used_as_access(auth):
    refresh = auth.create_refresh_token("alice")
    with pytest.raises(Exception):
        auth.decode_token(refresh, expected_type="access")
    claims = auth.decode_token(refresh, expected_type="refresh")
    assert claims["type"] == "refresh"


def test_access_token_cannot_refresh(auth):
    access = auth.create_token({"sub": "alice"})
    with pytest.raises(Exception):
        auth.refresh_access_token(access)


def test_revocation_blocks_reuse(auth):
    token = auth.create_token({"sub": "bob"})
    assert auth.decode_token(token)["sub"] == "bob"
    assert auth.revoke_token(token) is True
    with pytest.raises(Exception):
        auth.decode_token(token)


def test_no_hardcoded_secret_in_source():
    src = (Path(__file__).resolve().parents[1] / "backend" / "auth.py").read_text(encoding="utf-8")
    # The weak-key *list* may name these strings; assigning one must not happen.
    assert 'SECRET_KEY = "your-secret-key-here"' not in src
    assert "_load_secret_key" in src
    assert 'SECRET_KEY = "change-in-production"' not in src


def test_role_gate(auth):
    import asyncio

    viewer = auth.User(id="1", email="v", username="v", role="viewer")
    dep = auth.require_role("trader")

    async def _call():
        return await dep(viewer)

    with pytest.raises(Exception):
        asyncio.run(_call())


def test_api_routes_require_a_token():
    os.environ.setdefault("ZERO_SECRET_KEY", "unit-test-secret-key-0123456789abcdef")
    try:
        from fastapi.testclient import TestClient
        from backend.main import app
    except Exception as exc:  # pragma: no cover - optional dependency
        pytest.skip(f"backend deps unavailable: {exc}")

    client = TestClient(app)
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/live-prices").status_code == 401
    assert client.get("/api/v1/blotter").status_code == 401

    from backend import auth as auth_mod
    token = auth_mod.create_token({"sub": "tester", "uid": "u1", "role": "viewer"})
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["username"] == "tester"
    assert me.json()["role"] == "viewer"

    # refresh token in the bearer slot must be rejected
    refresh = auth_mod.create_refresh_token("tester")
    assert client.get("/api/v1/auth/me",
                      headers={"Authorization": f"Bearer {refresh}"}).status_code == 401
    # and a raw username is not a refresh token
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": "tester"}).status_code == 401


# ── 4. Selection bias ──────────────────────────────────────────────────────

def test_pbo_flags_pure_noise_selection():
    from engine.v2 import overfitting

    rng = np.random.default_rng(3)
    trials = {f"t{i}": list(rng.normal(0, 0.01, 400)) for i in range(8)}
    out = overfitting.pbo_cscv(trials, blocks=8)
    assert out["pbo"] is not None
    assert 0.0 <= out["pbo"] <= 1.0
    assert out["n_trials"] == 8


def test_deflated_sharpe_penalises_many_trials():
    from engine.v2 import overfitting

    rng = np.random.default_rng(5)
    # A real-looking edge: enough to look good, not enough to survive the
    # "best of 200 trials" benchmark untouched.
    modest = list(rng.normal(0.0016, 0.008, 300))
    few = overfitting.deflated_sharpe(modest, n_trials=2)
    many = overfitting.deflated_sharpe(modest, n_trials=200)
    assert many["benchmark_sharpe"] > few["benchmark_sharpe"]
    assert many["deflated_sharpe"] < few["deflated_sharpe"]
    assert 0.0 <= many["deflated_sharpe"] <= 1.0


# ── 5. Book risk ───────────────────────────────────────────────────────────

def test_book_risk_marks_unalignable_strategies():
    from engine.v2 import portfolio_risk

    runs = {
        "aligned": {"trades": [
            {"exit_date": "2024-01-02", "pnl": 1000.0},
            {"exit_date": "2024-01-03", "pnl": -500.0},
        ]},
        "barlike": {"trades": [{"exit_time": "bar_5", "pnl": 500.0}]},
    }
    risk = portfolio_risk.book_risk(runs)
    assert "barlike" in risk["unalignable"]
    assert risk["status"] == "OK"


def test_book_risk_gate_blocks_concentration():
    from engine.v2 import portfolio_risk

    rng = np.random.default_rng(9)
    runs = {}
    for i in range(3):
        trades = [{"exit_date": str(d)[:10], "pnl": float(p)} for d, p in zip(
            pd.bdate_range("2024-01-01", periods=60),
            rng.normal(200, 900, 60))]
        runs[f"s{i}"] = {"trades": trades}
    risk = portfolio_risk.book_risk(runs)
    assert risk["status"] == "OK"
    assert risk["largest_weight"] <= portfolio_risk.MAX_SINGLE_STRATEGY_WEIGHT + 1e-6


# ── 6. Cost model ──────────────────────────────────────────────────────────

def test_india_costs_are_charged_per_trade():
    from engine.v2 import costs

    rt = costs.round_trip_cost(24000.0, 24120.0, 75.0, 1, bars_held=1)
    assert rt["model"] == "india"
    assert rt["total"] > 0
    assert rt["segment"] == "equity_intraday"
    long_hold = costs.round_trip_cost(24000.0, 24120.0, 75.0, 1, bars_held=30)
    assert long_hold["segment"] == "equity_delivery"
    assert long_hold["total"] != rt["total"]


# ── 7. Point-in-time store ─────────────────────────────────────────────────

def test_pit_store_as_of_ignores_future_revisions(tmp_path):
    from data import pit_store

    idx = pd.bdate_range("2024-01-01", periods=10)
    base = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0,
                         "close": 100.5, "volume": 1000.0}, index=idx)
    pit_store.write("TESTIDX", base, vendor="vendor_a", known_at=1_700_000_000,
                    revision=1, root=str(tmp_path))

    revised = base.copy()
    revised["close"] = 200.0
    revised["high"] = 201.0
    cropped = revised.iloc[[9]]
    pit_store.write("TESTIDX", cropped, vendor="vendor_b",
                    known_at=1_800_000_000, revision=2, root=str(tmp_path))

    old = pit_store.as_of("TESTIDX", "2024-01-20", root=str(tmp_path))
    new = pit_store.as_of("TESTIDX", "2027-06-01", root=str(tmp_path))
    assert len(old) == len(new) == 10
    assert abs(old["close"].iloc[-1] - 100.5) < 1e-9, "future revision leaked into as_of"
    assert abs(new["close"].iloc[-1] - 200.0) < 1e-9


# ── 8. Order state machine ─────────────────────────────────────────────────

def test_order_state_machine_and_idempotency(tmp_path, monkeypatch):
    from engine.broker import order_state as osm

    monkeypatch.setattr(osm, "ORDER_DIR", str(tmp_path))
    monkeypatch.setattr(osm, "EVENTS_PATH", str(tmp_path / "order_events.jsonl"))
    monkeypatch.setattr(osm, "ORDERS_PATH", str(tmp_path / "orders.json"))
    monkeypatch.setattr(osm, "_orders", {})
    monkeypatch.setattr(osm, "_by_client_key", {})
    monkeypatch.setattr(osm, "_loaded", False)

    o1 = osm.create("NIFTY", "BUY", 75, client_key="k1")
    o2 = osm.create("NIFTY", "BUY", 75, client_key="k1")
    assert o1["order_id"] == o2["order_id"], "retry created a duplicate order"

    osm.transition(o1["order_id"], osm.VALIDATED, actor="risk")
    with pytest.raises(osm.IllegalTransition):
        osm.transition(o1["order_id"], osm.FILLED, actor="gateway")

    osm.transition(o1["order_id"], osm.SENT, actor="gateway")
    osm.transition(o1["order_id"], osm.ACKED, actor="broker")
    osm.transition(o1["order_id"], osm.FILLED, actor="broker", filled_quantity=75,
                   average_price=24000.0)
    assert osm.get(o1["order_id"])["state"] == osm.FILLED

    report = osm.reconcile([{"order_id": o1["order_id"], "state": "FILLED",
                             "filled_quantity": 50}])
    assert report["quantity_mismatch"]
    assert not report["clean"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
