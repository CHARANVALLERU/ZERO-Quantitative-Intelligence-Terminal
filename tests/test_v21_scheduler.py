"""
Offline tests for the v2.1 ecosystem scheduler and its bounded writes.

Nothing here touches a vendor: the cycle is injected, and every path is relative
to a ``tmp_path`` cwd, so a test run cannot append to the real ledgers.
"""
from __future__ import annotations

import json
import os
import time

import pytest

from engine.v2 import alerts as alerts_mod
from engine.v2 import shadow as shadow_mod
from engine.v21 import ecosystem
from engine.v21 import scheduler


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("ZERO_V21_SCHEDULER", "ZERO_V21_CYCLE_SECONDS", "ZERO_V21_CYCLE_INDEXES",
                 "ZERO_V21_FIRST_DELAY", "ZERO_V21_LOG_MAX_BYTES", "ZERO_V21_LEDGER_LIMIT",
                 "ZERO_V21_SPEC_LIMIT", "ZERO_ALERT_MAX_BYTES", "ZERO_ALERT_MAX_ROWS",
                 "ZERO_SHADOW_MAX_BYTES", "ZERO_SHADOW_MAX_ROWS"):
        monkeypatch.delenv(name, raising=False)
    scheduler.stop()
    yield
    scheduler.stop()


def _state() -> dict:
    with open(scheduler.state_path(), "r", encoding="utf-8") as fh:
        return json.load(fh)


# ── one pass ─────────────────────────────────────────────────────────────────

def test_run_once_records_success():
    rec = scheduler.run_once(
        index="NIFTY",
        cycle=lambda i: {"available": True, "suggestion": {"label": "bot · LONG"}},
    )
    assert rec["ok"] is True and rec["index"] == "NIFTY" and rec["error"] is None
    st = _state()
    assert st["runs"] == 1 and st["failures"] == 0 and st["consecutive_failures"] == 0
    assert st["last_ok"] is True and st["last_suggestion"] == "bot · LONG"


def test_run_once_survives_exception_and_tracks_streak():
    def boom(index: str):
        raise RuntimeError("vendor down")

    first = scheduler.run_once(index="SENSEX", cycle=boom)
    second = scheduler.run_once(index="SENSEX", cycle=boom)
    assert first["ok"] is False and "vendor down" in first["error"]
    assert second["ok"] is False
    st = _state()
    assert st["runs"] == 2 and st["failures"] == 2 and st["consecutive_failures"] == 2

    scheduler.run_once(index="SENSEX", cycle=lambda i: {"available": True})
    st = _state()
    assert st["consecutive_failures"] == 0 and st["failures"] == 2


def test_run_once_treats_unavailable_cycle_as_failure():
    rec = scheduler.run_once(index="NIFTY", cycle=lambda i: {"available": False, "reason": "no bars"})
    assert rec["ok"] is False and rec["error"] == "no bars"
    assert _state()["consecutive_failures"] == 1


def test_state_file_stays_small_over_many_runs():
    for _ in range(60):
        scheduler.run_once(index="NIFTY", cycle=lambda i: {"available": True, "suggestion": {"label": "x"}})
    st = _state()
    assert st["runs"] == 60
    assert set(st) == {
        "version", "runs", "failures", "consecutive_failures", "last_run_ts",
        "last_run_iso", "last_index", "last_ok", "last_seconds", "last_error",
        "last_suggestion", "updated_iso",
    }
    assert os.path.getsize(scheduler.state_path()) < 1500


def test_error_text_is_truncated():
    def boom(index: str):
        raise RuntimeError("z" * 2000)

    scheduler.run_once(index="NIFTY", cycle=boom)
    assert len(_state()["last_error"]) <= scheduler.ERROR_CHARS


# ── the run log ──────────────────────────────────────────────────────────────

def test_run_log_rotates_and_stays_bounded(monkeypatch):
    monkeypatch.setenv("ZERO_V21_LOG_MAX_BYTES", "1500")
    for _ in range(200):
        scheduler._log("x" * 120)
    files = [f"{scheduler.log_path()}{suffix}" for suffix in ("", ".1", ".2", ".3")]
    present = [p for p in files if os.path.exists(p)]
    assert 1 <= len(present) <= scheduler.LOG_BACKUPS + 1
    assert max(os.path.getsize(p) for p in present) <= 1500 + 400


# ── configuration ────────────────────────────────────────────────────────────

def test_interval_is_clamped_and_parses(monkeypatch):
    monkeypatch.setenv("ZERO_V21_CYCLE_SECONDS", "1")
    assert scheduler.interval_seconds() == scheduler.MIN_INTERVAL_SECONDS
    monkeypatch.setenv("ZERO_V21_CYCLE_SECONDS", "not-a-number")
    assert scheduler.interval_seconds() == scheduler.DEFAULT_INTERVAL_SECONDS


def test_indexes_are_canonical_and_deduped(monkeypatch):
    monkeypatch.setenv("ZERO_V21_CYCLE_INDEXES", "nifty, NIFTY , banknifty")
    keys = scheduler.indexes()
    assert keys == ["NIFTY", "BANKNIFTY"]


def test_enabled_switch(monkeypatch):
    monkeypatch.setenv("ZERO_V21_SCHEDULER", "off")
    assert scheduler.enabled() is False
    monkeypatch.setenv("ZERO_V21_SCHEDULER", "on")
    assert scheduler.enabled() is True


# ── the thread ───────────────────────────────────────────────────────────────

def test_thread_runs_a_pass_and_stops_on_demand():
    seen = []

    def runner(index: str):
        seen.append(index)
        return {"available": True, "suggestion": {"label": "threaded"}}

    scheduler.start(index="NIFTY", interval=60, runner=runner, first_delay=0)
    deadline = time.time() + 10
    while time.time() < deadline and scheduler.status()["runs"] < 1:
        time.sleep(0.05)
    st = scheduler.status()
    assert st["running"] is True
    assert st["runs"] >= 1 and seen == ["NIFTY"]
    scheduler.stop()
    assert scheduler.status()["running"] is False


def test_start_is_idempotent():
    runner = lambda i: {"available": True}  # noqa: E731
    scheduler.start(index="NIFTY", interval=60, runner=runner, first_delay=5)
    thread = scheduler._THREAD
    scheduler.start(index="SENSEX", interval=60, runner=runner, first_delay=5)
    assert scheduler._THREAD is thread
    assert scheduler.status()["indexes"] == ["NIFTY"]


def test_status_reports_paths_and_counters():
    st = scheduler.status()
    assert st["state_path"] == scheduler.state_path()
    assert st["log_path"] == scheduler.log_path()
    assert st["runs"] == 0 and st["failures"] == 0


# ── bounded ecosystem ledger + specs ─────────────────────────────────────────

def _record(n: int) -> dict:
    return {"index": "NIFTY", "generated_at": f"t{n}", "seconds": 1.0,
            "suggestion": {"label": "x"}, "regime": {"label": "up-trend"}}


def test_ecosystem_ledger_keeps_only_newest(monkeypatch):
    monkeypatch.setenv("ZERO_V21_LEDGER_LIMIT", "5")
    for n in range(20):
        ecosystem._write_ledger(_record(n))
    with open(ecosystem.LEDGER, "r", encoding="utf-8") as fh:
        rows = json.load(fh)
    assert len(rows) == 5
    assert [r["generated_at"] for r in rows] == [f"t{n}" for n in range(15, 20)]
    assert ecosystem.history(limit=3)[-1]["generated_at"] == "t19"


def test_ledger_write_is_atomic(monkeypatch):
    monkeypatch.setenv("ZERO_V21_LEDGER_LIMIT", "5")
    ecosystem._write_ledger(_record(0))
    assert not os.path.exists(ecosystem.LEDGER + ".tmp")


def test_spec_files_are_pruned_per_index(monkeypatch):
    monkeypatch.setenv("ZERO_V21_SPEC_LIMIT", "3")
    for n in range(6):
        best = {"bot_id": f"bot_{n:02d}", "style": "trend", "genome": {"n": n},
                "oos_sharpe": 1.0, "passed": True, "max_dd": 0.1, "trades_oos": 5}
        ecosystem._persist_spec("NIFTY", best, {"label": "up"}, {"bias": "LONG"})
    names = sorted(n for n in os.listdir(ecosystem.V21_STRATEGY_DIR))
    assert len(names) == 3
    assert all(n.startswith("nifty_bot_") for n in names)


# ── bounded alert + shadow logs ──────────────────────────────────────────────

def test_alert_log_is_capped(monkeypatch):
    monkeypatch.setenv("ZERO_ALERT_MAX_BYTES", "1200")
    monkeypatch.setenv("ZERO_ALERT_MAX_ROWS", "10")
    for n in range(50):
        alerts_mod._append_log({"n": n, "title": "x" * 80})
    with open(alerts_mod.ALERTS_PATH, "r", encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    assert 0 < len(lines) <= 10
    assert json.loads(lines[-1])["n"] == 49
    assert os.path.getsize(alerts_mod.ALERTS_PATH) <= 1200 + 200


def test_shadow_log_is_capped(monkeypatch):
    monkeypatch.setenv("ZERO_SHADOW_MAX_BYTES", "1200")
    monkeypatch.setenv("ZERO_SHADOW_MAX_ROWS", "8")
    for n in range(60):
        assert shadow_mod._append(shadow_mod.SIGNALS_PATH, {"n": n, "side": "LONG", "pad": "x" * 70})
    with open(shadow_mod.SIGNALS_PATH, "r", encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    assert 0 < len(lines) <= 8
    assert json.loads(lines[-1])["n"] == 59
