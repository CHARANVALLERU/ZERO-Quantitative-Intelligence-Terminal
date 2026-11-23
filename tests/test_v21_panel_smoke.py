"""
Headless render test for the v2.1 panel.

Proves the panel actually renders inside Streamlit (not just imports):
the index selector and the **⚡ Run all** button exist, no exception is raised,
and - when `ZERO_UI_SMOKE=1` - pressing Run all produces the consensus card.

The full click path hits live vendors (GEX + the ecosystem cycle), so it is opt-in.
"""
from __future__ import annotations

import os

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

APP = os.path.join(os.path.dirname(__file__), "_v21_app.py")


def _run():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, [e.message for e in at.exception]
    return at


def test_panel_renders_with_selector_and_run_all():
    at = _run()
    labels = [b.label for b in at.button]
    assert "\u26a1 Run all" in labels
    assert at.selectbox[0].options == ["NIFTY 50", "SENSEX", "BANKNIFTY"]
    assert [t.label for t in at.tabs][:2] == ["Suggestions", "Trading"]


def test_index_selector_switches_index():
    at = _run()
    at.selectbox[0].select("BANKNIFTY").run()
    assert not at.exception, [e.message for e in at.exception]
    assert at.selectbox[0].value == "BANKNIFTY"
    # The panel echoes the selected index in the quote strip (or its reason card).
    body = " ".join(m.value for m in at.markdown)
    assert "BANKNIFTY" in body


def test_action_buttons_do_not_collide_with_state(monkeypatch):
    """A button key must never be the session key that same button writes.

    Regression: the Trading and SEC buttons used one key for both, so a click
    raised ``StreamlitAPIException: st.session_state.<key> cannot be modified
    after the widget with key <key> is instantiated``. Vendors and quotes are
    stubbed so the clicks stay offline.
    """
    from engine.v21 import agents as agents_mod
    from engine.v21 import indices, trading

    monkeypatch.setattr(indices, "quote",
                        lambda key: {"available": False, "reason": "offline"})
    monkeypatch.setattr(trading, "fno_board", lambda key: {
        "gex": {"available": False}, "chain": {"error": "offline"},
        "source": "stub", "delay": "n/a"})
    monkeypatch.setattr(trading, "option_table", lambda key, **kw: [])
    monkeypatch.setattr(trading, "commodities",
                        lambda: {"available": False, "error": "offline"})
    monkeypatch.setattr(trading, "heatmap",
                        lambda key: {"available": False, "error": "offline"})
    for name in ("debate", "insider_clusters", "earnings_stress"):
        monkeypatch.setattr(agents_mod, name, lambda ticker, **kw: {})

    at = AppTest.from_file(APP, default_timeout=120).run()
    labels = ("Refresh F&O board", "Load option ladder", "Commodities tape",
              "Sector heatmap", "Bull vs bear", "Insider clusters", "Earnings stress")
    for label in labels:
        next(b for b in at.button if b.label == label).click().run()
        assert not at.exception, (label, [e.message for e in at.exception])


@pytest.mark.skipif(os.getenv("ZERO_UI_SMOKE") != "1",
                    reason="runs live GEX + ecosystem cycle; set ZERO_UI_SMOKE=1")
def test_run_all_produces_a_labelled_consensus():
    at = _run()
    run_all = next(b for b in at.button if b.label == "\u26a1 Run all")
    run_all.click().run(timeout=240)
    assert not at.exception, [e.message for e in at.exception]
    text = " ".join(m.value for m in at.markdown)
    assert "CONSENSUS" in text or "MIXED" in text
