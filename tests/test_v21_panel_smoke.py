"""
Headless render test for the v2.1 panel.

Proves the panel actually renders inside Streamlit (not just imports):
the index selector and the **⚡ Run all** button exist, no exception is raised,
and - when `ZERO_UI_SMOKE=1` - pressing Run all produces the consensus card.

The full click path hits live vendors (GEX + the ecosystem cycle), so it is opt-in.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

APP = os.path.join(os.path.dirname(__file__), "_v21_app.py")
PANEL = Path(__file__).resolve().parents[1] / "ui" / "v21_panel.py"


# ── static key-collision scan ──────────────────────────────────────────────
# A key pattern is a tuple of literal segments; ``None`` marks one interpolated
# f-string field.  "abc" -> ("abc",)   f"a{slot}b" -> ("a", None, "b")

def _pattern(node):
    """Literal / f-string key node -> pattern; anything else -> None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.JoinedStr):
        segs = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                segs.append(part.value)
            else:
                segs.append(None)
        return tuple(segs)
    return None


def _show(pattern) -> str:
    if None not in pattern:
        return repr(pattern[0])
    return 'f"' + "".join("{}" if s is None else s for s in pattern) + '"'


def _is_session_state(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "session_state"
            and isinstance(node.value, ast.Name) and node.value.id == "st")


def _collect(source):
    """Scan source -> (widget keys, session writes) as [lineno, pattern] pairs."""
    tree = ast.parse(source)
    widgets, writes = [], []

    def add(bucket, node, lineno):
        p = _pattern(node)
        if p is not None:
            bucket.append((lineno, p))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "key":
                    add(widgets, kw.value, node.lineno)
            fn = node.func
            if isinstance(fn, ast.Attribute) and _is_session_state(fn.value):
                if fn.attr == "update":
                    for arg in node.args:
                        if isinstance(arg, ast.Dict):
                            for k in arg.keys:
                                if k is not None:
                                    add(writes, k, node.lineno)
                elif fn.attr in {"setdefault", "pop"} and node.args:
                    add(writes, node.args[0], node.lineno)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _is_session_state(target.value):
                    add(writes, target.slice, node.lineno)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            target = node.target
            if isinstance(target, ast.Subscript) and _is_session_state(target.value):
                add(writes, target.slice, node.lineno)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and _is_session_state(target.value):
                    add(writes, target.slice, node.lineno)
    return widgets, writes


def _can_collide(a, b) -> bool:
    """True when some runtime values make pattern ``a`` and ``b`` one string."""
    fixed_a, fixed_b = None not in a, None not in b
    if fixed_a and fixed_b:
        return a == b
    if fixed_a or fixed_b:
        fixed, templated = (a, b) if fixed_a else (b, a)
        regex = "".join(".*" if s is None else re.escape(s) for s in templated)
        return re.fullmatch(regex, fixed[0], re.DOTALL) is not None
    # Both dynamic: an identical skeleton is decided (same values -> same key).
    # Two different skeletons could still meet if a hole swallows the other
    # side's literal; that is the documented gap of this scan.
    return a == b


def _collisions(source):
    widgets, writes = _collect(source)
    return [(wl, wp, sl, sp) for wl, wp in widgets for sl, sp in writes
            if _can_collide(wp, sp)]


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


def test_no_widget_key_can_equal_a_session_key():
    """Static guard over the whole panel, f-string keys included.

    Regression: eight buttons (Trading F&O/ladder/commodities/heatmap and SEC
    debate/insider/stress) used one string for both the widget key and the
    session slot they wrote, so a click raised ``StreamlitAPIException``. The
    runtime click test only covers buttons it presses; this scan covers every
    widget call in the file, including the f-string keys built in the ``_desk``
    and forecast helpers.
    """
    found = _collisions(PANEL.read_text(encoding="utf-8"))
    assert not found, "\n".join(
        f"ui/v21_panel.py:{wl}: widget key {_show(wp)} can equal the session key "
        f"written at line {sl}: {_show(sp)}"
        for wl, wp, sl, sp in found)


def test_scan_sees_dynamic_keys_and_flags_collisions():
    """Prove the guard is not blind to f-string keys (and has no false alarm)."""
    colliding = '''
import streamlit as st

def desk(name):
    if st.button("Load", key=f"v21_desk_btn_{name}"):
        st.session_state[f"v21_desk_btn_{name}"] = 1
'''
    assert len(_collisions(colliding)) == 1

    literalized = '''
import streamlit as st

if st.button("Go", key=f"v21_{slot}"):
    st.session_state["v21_fno"] = 1
'''
    assert len(_collisions(literalized)) == 1

    safe = '''
import streamlit as st

def desk(name):
    if st.button("Load", key=f"v21_desk_btn_{name}"):
        st.session_state[f"v21_desk_{name}"] = 1
'''
    assert _collisions(safe) == []
    widgets, _ = _collect(safe)
    assert any(None in pattern for _, pattern in widgets), \
        "f-string widget keys were not collected"


@pytest.mark.skipif(os.getenv("ZERO_UI_SMOKE") != "1",
                    reason="runs live GEX + ecosystem cycle; set ZERO_UI_SMOKE=1")
def test_run_all_produces_a_labelled_consensus():
    at = _run()
    run_all = next(b for b in at.button if b.label == "\u26a1 Run all")
    run_all.click().run(timeout=240)
    assert not at.exception, [e.message for e in at.exception]
    text = " ".join(m.value for m in at.markdown)
    assert "CONSENSUS" in text or "MIXED" in text
