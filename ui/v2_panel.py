"""
ZERO Engine v2.0 — Panel UI
===========================

The single home for everything added in v2.0:

``MACRO FACTOR GRAPH`` · ``UNIFIED AGGREGATION`` · ``MARKET STRUCTURE`` ·
``STRATEGY LAB`` · ``INSTITUTIONAL RESEARCH`` · ``MCP / REA`` · ``INTEGRITY``

Every section is lazy: network work only happens when the operator presses the
section's action button, so opening the tab never blocks the terminal. All
panels degrade to an explanatory line when an optional piece (REA, history,
network) is unavailable.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

_P = {
    "void": "#000000",
    "panel": "#0a0a0a",
    "crimson": "#E50914",
    "gold": "#D4AF37",
    "neon": "#00ff88",
    "green": "#00E676",
    "yellow": "#FFD600",
    "white": "#ffffff",
    "mute": "#666666",
}

_CACHE_TTL = 300.0


# ═══════════════════════════════════════════════════════════════════════════
#  Small helpers
# ═══════════════════════════════════════════════════════════════════════════

def _card(title: str, body_html: str, accent: str = _P["gold"]) -> None:
    st.markdown(
        f"""
        <div style="background:{_P['panel']};border:1px solid #1c1c1c;
                    border-left:3px solid {accent};border-radius:6px;
                    padding:14px 16px;margin-bottom:12px;">
          <div style="color:{accent};font-size:0.72rem;letter-spacing:0.14em;
                      font-weight:700;margin-bottom:8px;">{title}</div>
          <div style="color:#cfcfcf;font-size:0.82rem;line-height:1.55;">{body_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _kv(value: Any, fmt: str = "{:,.2f}") -> str:
    if value is None:
        return "—"
    try:
        return fmt.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _status_chip(label: str, ok: bool, detail: str = "") -> str:
    colour = _P["neon"] if ok else _P["crimson"]
    text = "OK" if ok else "ATTENTION"
    return (
        f"<span style='color:{colour};font-weight:700;'>{label} · {text}</span>"
        f" <span style='color:{_P['mute']};'>{detail}</span>"
    )


def _cached(key: str, builder, ttl: float = _CACHE_TTL):
    """Session-scoped memo with a TTL, so reruns do not repeat work."""
    now = _dt.datetime.now().timestamp()
    entry = st.session_state.get(f"_v2cache_{key}")
    if isinstance(entry, dict) and now - entry.get("ts", 0) < ttl:
        return entry.get("value")
    value = builder()
    st.session_state[f"_v2cache_{key}"] = {"ts": now, "value": value}
    return value


def _fmt_pct(value: Optional[float], nd: int = 3) -> str:
    return "—" if value is None else f"{value:+.{nd}f}%"


# ═══════════════════════════════════════════════════════════════════════════
#  Daily brief toast + folder navigator
# ═══════════════════════════════════════════════════════════════════════════

def maybe_render_brief() -> None:
    """Fire the once-per-day admin brief, with the strategies folder jump.

    Called from app.py before the tabs so it appears at the very beginning of
    the session, in the terminal's own styling.
    """
    try:
        from engine.v2 import session_brief
        from engine.v2 import strategy_lab
    except Exception:
        return

    try:
        payload = session_brief.toast_payload()
    except Exception:
        return

    if payload.get("show"):
        try:
            st.toast(f"ZERO v2.0 — {payload['summary']}", icon="⚡")
        except Exception:
            pass
        try:
            session_brief.mark_briefed()
        except Exception:
            pass

    top = payload.get("top") or []
    top_html = "".join(
        f"<div style='color:{_P['neon']};font-size:0.78rem;'>"
        f"▲ {t.get('name')} · {t.get('index')} · PF {t.get('profit_factor')} · "
        f"{_fmt_pct(t.get('return_pct'), 2)}</div>"
        for t in top
    ) or (
        f"<div style='color:{_P['mute']};font-size:0.78rem;'>"
        "No passing strategy yet — run the Strategy Lab to generate the first batch.</div>"
    )

    col_text, col_btn = st.columns([9, 1])
    with col_text:
        st.markdown(
            f"""
            <div style="background:linear-gradient(90deg,#0a0a0a 0%,#111 100%);
                        border:1px solid #1c1c1c;border-left:3px solid {_P['gold']};
                        border-radius:6px;padding:12px 16px;margin:6px 0 14px 0;">
              <div style="color:{_P['gold']};font-size:0.70rem;letter-spacing:0.18em;
                          font-weight:700;">ZERO ENGINE v2.0 · SESSION BRIEF · {payload.get('date','')}</div>
              <div style="color:#e8e8e8;font-size:0.86rem;margin-top:6px;">
                {payload.get('strategies_created', 0)} strategies created ·
                <span style="color:{_P['neon']};">{payload.get('strategies_passed', 0)} passed</span> ·
                {payload.get('improved', 0)} improved ·
                integrity {payload.get('health', {}).get('pct', '—')}%
              </div>
              <div style="margin-top:8px;">{top_html}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with col_btn:
        st.markdown("<div style='height:26px'></div>", unsafe_allow_html=True)
        if st.button("→", key="v2_brief_open_folder", help="Open the strategies folder"):
            strategy_lab.open_folder()


# ═══════════════════════════════════════════════════════════════════════════
#  Section renderers
# ═══════════════════════════════════════════════════════════════════════════

def _render_macro() -> None:
    from engine.v2 import macro_factors

    snap = st.session_state.get("v2_macro_snapshot") or macro_factors.load_last_snapshot()
    prev = macro_factors.load_last_snapshot()

    c1, c2, c3 = st.columns([2, 2, 2])
    with c1:
        if st.button("REFRESH MACRO GRAPH", key="v2_macro_refresh", width="stretch"):
            with st.spinner("Fetching crude, gold, DXY, USDINR…"):
                fresh = macro_factors.fetch_snapshot(allow_network=True)
                if fresh.coverage() > 0:
                    macro_factors.save_snapshot(fresh)
                st.session_state["v2_macro_snapshot"] = fresh
                snap = fresh
                prev = macro_factors.load_last_snapshot()
            st.rerun()
    with c2:
        manual = macro_factors.load_macro_inputs()
        st.caption(
            f"Manual inputs: CPI {manual.get('cpi_yoy_pct', '—')}% · "
            f"target {manual.get('cpi_target_pct', '—')}% · "
            f"supply gap {manual.get('supply_gap_pct', '—')}%"
        )
    with c3:
        st.caption(f"Store: db/v2/macro_inputs.json · updated {manual.get('updated_at', '—')}")

    if snap is None:
        _card(
            "MACRO GRAPH — NO SNAPSHOT",
            "Press <b>REFRESH MACRO GRAPH</b> to build the first snapshot. "
            "The graph needs a previous point to compute changes; the first run "
            "establishes the baseline and will report low confidence by design.",
            accent=_P["yellow"],
        )
        return

    analysis = macro_factors.analyze(snap, prev)
    net = analysis.get("net_pct", {})
    conf = analysis.get("confidence", 0.0)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("REGIME", str(analysis.get("regime", "—")).replace("_", " "))
    m2.metric("COVERAGE", f"{analysis.get('coverage', 0):.0%}")
    m3.metric("CONFIDENCE", f"{conf:.0%}")
    m4.metric("APPLIED", "YES" if analysis.get("apply") else "NO (low confidence)")

    st.markdown(
        f"<p class='label-grey' style='margin:6px 0 10px 0;'>Rule audit — each "
        f"relationship the desk specified, checked against today's tape.</p>",
        unsafe_allow_html=True,
    )
    audit_df = pd.DataFrame(analysis.get("rule_audit", []))
    if not audit_df.empty:
        st.dataframe(
            audit_df.rename(columns={
                "rule": "RULE", "statement": "RELATIONSHIP",
                "observed": "OBSERVED", "status": "STATUS",
            }),
            width="stretch", hide_index=True,
        )

    col_a, col_b = st.columns([3, 2])
    with col_a:
        st.markdown("<p class='label-grey'>CHANNEL CONTRIBUTIONS (% of index)</p>", unsafe_allow_html=True)
        contrib = analysis.get("channel_contributions_pct", {})
        if contrib:
            df = pd.DataFrame(
                [{"channel": k, "contribution_pct": v} for k, v in contrib.items()]
            ).sort_values("contribution_pct")
            st.bar_chart(df.set_index("channel"), height=240)
        else:
            st.info("No channel produced a value — inputs are missing.")
    with col_b:
        st.markdown("<p class='label-grey'>NET TILT BY INDEX</p>", unsafe_allow_html=True)
        for index, value in net.items():
            st.metric(index, f"{value:+.3f}%")
        st.caption(
            "Hard-capped at ±0.60% so the macro layer nudges the GIFT/ADR core "
            "and never overrides it."
        )

    st.markdown("<p class='label-grey'>DESK NARRATIVE</p>", unsafe_allow_html=True)
    for line in analysis.get("narrative", []):
        st.markdown(f"- {line}")

    if analysis.get("missing"):
        st.caption("Missing inputs: " + ", ".join(analysis["missing"]))
    if analysis.get("notes"):
        for note in analysis["notes"]:
            st.caption(f"⚠ {note}")


def _render_aggregation() -> None:
    from engine.v2 import aggregator

    cached_view = st.session_state.get("v2_unified")
    c1, c2 = st.columns([2, 3])
    with c1:
        if st.button("SYNC UNIVERSE", key="v2_universe_sync", width="stretch"):
            with st.spinner("Fetching NSE + BSE quotes…"):
                st.session_state["v2_unified"] = aggregator.build_unified_view(allow_network=True)
            st.rerun()
    with c2:
        st.caption(
            "Quote cache: db/v2/quotes_cache.json (60 s). Event signals: "
            "db/v2/institutional_signals.json."
        )

    view = cached_view or aggregator.build_unified_view(allow_network=False)
    breadth = view.get("breadth", {})
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("ADVANCES", breadth.get("advances", 0))
    b2.metric("DECLINES", breadth.get("declines", 0))
    b3.metric("BREADTH", f"{breadth.get('score') or 0:+.2f}")
    b4.metric("SOURCES COVERED", f"{view.get('coverage', 0):.0%}")

    rows = view.get("primary") or []
    if rows:
        df = pd.DataFrame([{
            "SYMBOL": r.get("base_symbol"),
            "EXCHANGE": r.get("exchange"),
            "PRICE": r.get("price"),
            "CHG %": r.get("change_pct"),
            "COMPOSITE": r.get("composite_score"),
            "COVERAGE": r.get("coverage"),
        } for r in rows])
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("No cached quotes. Press SYNC UNIVERSE (needs network access).")

    with st.expander("SOURCE REGISTRY"):
        st.dataframe(pd.DataFrame(view.get("sources", [])), width="stretch", hide_index=True)
    with st.expander("SIGNALS PRESENT"):
        st.json(view.get("signals_present", {}))


def _render_structure() -> None:
    from engine.v2 import market_structure

    if st.button("STUDY NIFTY 50 + SENSEX", key="v2_structure_run", width="stretch"):
        with st.spinner("Loading history and scanning for whale footprints…"):
            studies: Dict[str, Any] = {}
            try:
                from data.historical import get_historical_data
                for key in ("NIFTY", "SENSEX"):
                    studies[key] = market_structure.study(get_historical_data(key, period="1y"), key)
            except Exception as exc:
                st.error(f"History unavailable: {exc}")
            st.session_state["v2_studies"] = studies
        st.rerun()

    studies = st.session_state.get("v2_studies") or {}
    if not studies:
        _card(
            "MARKET STRUCTURE — IDLE",
            "Run the study to scan 80 sessions for absorption, sliced execution, "
            "liquidity sweeps and initiative aggression, then classify the Wyckoff phase. "
            "Order-book depth is <b>not</b> available; the panel says so instead of pretending.",
        )
        return

    for name, study in studies.items():
        st.markdown(f"<p class='gold-title' style='font-size:0.95rem;'>{name}</p>", unsafe_allow_html=True)
        if study.get("status") != "OK":
            st.warning(" · ".join(study.get("narrative", ["no data"])))
            continue
        phase = study.get("phase", {})
        levels = study.get("levels", {})
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("PHASE", phase.get("phase", "—"))
        c2.metric("RANGE POSITION", f"{phase.get('range_position', 0):.0%}")
        c3.metric("ATR(14)", _kv(levels.get("atr14"), "{:,.0f}"))
        c4.metric("EVENTS", len(study.get("events", [])))
        for line in study.get("narrative", []):
            st.markdown(f"- {line}")
        if study.get("events"):
            st.dataframe(
                pd.DataFrame([{
                    "DATE": e["date"], "TYPE": e["type"], "BIAS": e["bias"],
                    "CONF": e["confidence"], "NOTE": e["note"],
                } for e in study["events"]]),
                width="stretch", hide_index=True,
            )
        st.caption("Limits: " + " · ".join(study.get("limits", [])))
        st.divider()


def _render_strategy_lab() -> None:
    from engine.v2 import strategy_lab

    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        improve = st.checkbox("Improve winners (OOS-validated)", value=True, key="v2_lab_improve")
    with c2:
        run = st.button("RUN STRATEGY LAB", key="v2_lab_run", width="stretch")
    with c3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("→ FOLDER", key="v2_lab_open", help="Open the strategies folder"):
            strategy_lab.open_folder()

    if run:
        with st.spinner("Generating, backtesting and writing strategy documents…"):
            try:
                result = strategy_lab.run_lab(improve_winners=improve)
                st.session_state["v2_lab_result"] = result
            except Exception as exc:
                st.error(f"Strategy lab failed: {exc}")

    registry = strategy_lab.load_registry()
    result = st.session_state.get("v2_lab_result")
    if result and result.get("created"):
        st.success(
            f"Run complete — {result['created']} strategies written, "
            f"{result['passed']} passed, {result['improved']} improved."
        )
        if result.get("skipped"):
            with st.expander(f"SKIPPED ({len(result['skipped'])})"):
                st.json(result["skipped"])

    if not registry.get("total"):
        _card(
            "STRATEGY LAB — NO STRATEGIES YET",
            "Press <b>RUN STRATEGY LAB</b>. The engine will generate rule sets across "
            "momentum, mean reversion, pullback, squeeze, absorption and sweep families, "
            "backtest each on NIFTY and SENSEX with ATR stops and realistic costs, then write "
            "<code>strategies/&lt;ID&gt;.md</code> and "
            "<code>strategies/_@&lt;ID&gt;_, mistakes and P&amp;L.md</code>.",
        )
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CREATED", registry.get("total", 0))
    m2.metric("PASSED", registry.get("passed", 0))
    m3.metric("IMPROVED", registry.get("improved", 0))
    m4.metric("UPDATED", str(registry.get("updated_at", "—"))[:10])

    st.markdown("<p class='label-grey'>TOP 3 (profit factor × sample weight)</p>", unsafe_allow_html=True)
    top = registry.get("top") or []
    if top:
        st.dataframe(pd.DataFrame([{
            "STRATEGY": t.get("name"), "INDEX": t.get("index"), "FAMILY": t.get("family"),
            "PF": (t.get("metrics") or {}).get("profit_factor"),
            "RETURN %": (t.get("metrics") or {}).get("return_pct"),
            "TRADES": (t.get("metrics") or {}).get("trades"),
            "MAX DD %": (t.get("metrics") or {}).get("max_drawdown_pct"),
        } for t in top]), width="stretch", hide_index=True)

    all_rows = [{
        "STRATEGY": s.get("name"),
        "INDEX": s.get("index"),
        "FAMILY": s.get("family"),
        "STATUS": s.get("status"),
        "PF": (s.get("metrics") or {}).get("profit_factor"),
        "RETURN %": (s.get("metrics") or {}).get("return_pct"),
        "TRADES": (s.get("metrics") or {}).get("trades"),
        "IMPROVED FROM": s.get("improved_from") or "—",
    } for s in registry.get("strategies", [])]
    st.dataframe(pd.DataFrame(all_rows), width="stretch", hide_index=True)

    names = [s.get("name") for s in registry.get("strategies", [])]
    selected = st.selectbox("Inspect a strategy document", ["—"] + names, key="v2_lab_doc")
    if selected != "—":
        entry = next((s for s in registry["strategies"] if s.get("name") == selected), {})
        doc = entry.get("doc")
        if doc and os.path.isfile(doc):
            with open(doc, "r", encoding="utf-8") as fh:
                st.markdown(fh.read())
        mistakes = entry.get("mistakes_doc")
        if mistakes and os.path.isfile(mistakes):
            with st.expander("MISTAKES & P&L"):
                with open(mistakes, "r", encoding="utf-8") as fh:
                    st.markdown(fh.read())


def _render_research() -> None:
    from engine.v2 import aggregator, macro_factors, research_agents

    if st.button("RUN RESEARCH DESKS", key="v2_research_run", width="stretch"):
        with st.spinner("Convening macro, flow, sector, policy and risk desks…"):
            snap = st.session_state.get("v2_macro_snapshot") or macro_factors.load_last_snapshot()
            macro = macro_factors.analyze(snap, macro_factors.load_last_snapshot()) if snap else {}
            unified = st.session_state.get("v2_unified") or aggregator.build_unified_view(allow_network=False)
            studies = st.session_state.get("v2_studies") or {}
            st.session_state["v2_desks"] = research_agents.run_desks(macro, unified, studies)
        st.rerun()

    desks = st.session_state.get("v2_desks")
    if not desks:
        _card(
            "INSTITUTIONAL RESEARCH — IDLE",
            "Each desk reads the merged evidence and the macro graph and returns a headline, "
            "numbered findings, falsification risks and a coverage-driven confidence. "
            "Press <b>RUN RESEARCH DESKS</b>. Memos are saved to db/v2/research/.",
        )
        return

    for desk in desks:
        with st.expander(f"{desk['label']} — {desk['headline']}", expanded=False):
            st.caption(desk.get("remit", ""))
            for finding in desk.get("findings", []):
                st.markdown(f"- {finding}")
            st.markdown(f"**Confidence:** {desk.get('confidence', 0):.0%}")
            if desk.get("risks"):
                st.markdown("**What would falsify this view**")
                for risk in desk["risks"]:
                    st.markdown(f"- {risk}")
            st.caption("Sources: " + ", ".join(desk.get("sources") or ["—"]))


def _render_mcp() -> None:
    from engine.v2 import rea_bridge

    status = rea_bridge.status()
    env = status.get("environment", {})
    state = status.get("state")
    c1, c2 = st.columns([2, 3])
    with c1:
        if st.button("DISCOVER REA TOOLS", key="v2_rea_probe", width="stretch"):
            with st.spinner("Starting REA over MCP and listing tools…"):
                st.session_state["v2_rea_probe"] = rea_bridge.probe_rea()
            st.rerun()
    with c2:
        st.caption(
            f"Node: {env.get('node') or 'not found'} · npx: {env.get('npx') or 'not found'} · "
            f"local REA: {env.get('rea_path') or 'not found'}"
        )

    colour = _P["neon"] if state == "CONNECTED" else _P["yellow"]
    _card(
        "MCP RESEARCH BRIDGE",
        _status_chip("REA", state == "CONNECTED", f"{status.get('tool_count', 0)} tools cached")
        + f"<br/>Registry: <code>config/mcp_servers.json</code> · transport: stdio JSON-RPC 2.0"
        + (f"<br/><span style='color:{_P['mute']};'>{status.get('error')}</span>"
           if status.get("error") else "")
        + "".join(
            f"<br/><code style='color:{_P['mute']};font-size:0.72rem;'>{line}</code>"
            for line in (status.get("stderr_tail") or [])[-3:]
        ),
        accent=colour,
    )

    capabilities = status.get("capabilities") or {}
    if capabilities:
        st.markdown("<p class='label-grey'>DISCOVERED CAPABILITIES</p>", unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([
            {"capability": k, "tools": ", ".join(v)} for k, v in capabilities.items()
        ]), width="stretch", hide_index=True)
    else:
        st.info(
            "No REA catalogue cached yet. Press DISCOVER REA TOOLS. If npx is missing, "
            "the bridge stays offline and the rest of the engine is unaffected."
        )

    st.markdown("<p class='label-grey'>RUN A REVERSE-ENGINEERING TASK</p>", unsafe_allow_html=True)
    r1, r2, r3 = st.columns([3, 2, 1])
    with r1:
        target = st.text_input("Target path or URL", key="v2_rea_target",
                               placeholder=r"D:\path\to\app.asar  or  https://host/api")
    with r2:
        intent = st.selectbox("Capability", list(rea_bridge.CAPABILITY_KEYWORDS), key="v2_rea_intent")
    with r3:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        go = st.button("RUN", key="v2_rea_run", width="stretch")
    if go:
        with st.spinner("REA is analysing the target…"):
            st.session_state["v2_rea_result"] = rea_bridge.reverse_engineer(target, intent)
    result = st.session_state.get("v2_rea_result")
    if result:
        if result.get("ok"):
            st.success(f"REA · {result.get('tool')} completed.")
            with st.expander("RESULT"):
                st.code(result.get("result_preview") or json.dumps(result.get("result"), indent=2)[:8000])
        else:
            st.warning(result.get("error") or "REA did not return a result.")
            if result.get("available_tools"):
                st.caption("Available tools: " + ", ".join(result["available_tools"][:40]))


def _render_integrity() -> None:
    from engine.v2 import state_audit

    report = _cached("audit", state_audit.audit, ttl=180)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("HEALTH", f"{report.get('health_pct', 0):.0f}%")
    m2.metric("CHECKS PASSED", f"{report.get('passed', 0)}/{report.get('total', 0)}")
    m3.metric("FAILED", report.get("failed", 0))
    m4.metric("HIGH SEVERITY", report.get("high", 0))

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("RUN SAFE REPAIRS", key="v2_audit_repair", width="stretch"):
            result = state_audit.repair(apply=True)
            st.session_state.pop("_v2cache_audit", None)
            st.success(f"{result['changed']} repair(s) applied, {result['pending']} pending.")
            with st.expander("REPAIR LOG"):
                st.json(result)
    with c2:
        st.caption(
            "Repairs are reversible and data-safe: NaN → null, phantom daemon state → stopped, "
            "missing __init__.py markers. Nothing is deleted."
        )

    checks = report.get("checks", [])
    if checks:
        df = pd.DataFrame([{
            "CHECK": c["name"], "STATUS": "OK" if c["ok"] else c["severity"],
            "DETAIL": c["detail"],
        } for c in checks])
        st.dataframe(df, width="stretch", hide_index=True)

    with st.expander("SYSTEM TOPOLOGY (v2.0)"):
        st.markdown(
            f"""
- **Prediction core** — `engine/prediction_matrix.py` (GIFT/ADR gap → IV envelope → OI walls →
  sentiment → news overlay → ridge calibration), now followed by the v2.0 macro overlay.
- **Macro graph** — `engine/v2/macro_factors.py` (11 rules, capped at ±0.60%).
- **Aggregation** — `engine/v2/aggregator.py` (NSE/BSE universe + event signals).
- **Structure** — `engine/v2/market_structure.py` (whale footprints, Wyckoff phase).
- **Strategy lab** — `engine/v2/strategy_lab.py` → `strategies/`.
- **Research desks** — `engine/v2/research_agents.py` → `db/v2/research/`.
- **MCP / REA** — `engine/v2/mcp_client.py`, `engine/v2/rea_bridge.py`.
- **Integrity** — `engine/v2/state_audit.py`.
            """
        )


# ═══════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════

def render_v2_panel(matrix: Optional[Dict[str, Any]] = None) -> None:
    """Render the whole ZERO Engine v2.0 tab."""
    st.markdown(
        f"""
        <div style="border-bottom:1px solid #1c1c1c;padding-bottom:10px;margin-bottom:14px;">
          <div style="color:{_P['gold']};font-size:1.05rem;letter-spacing:0.22em;font-weight:800;">
            ZERO ENGINE v2.0
          </div>
          <div style="color:{_P['mute']};font-size:0.76rem;margin-top:4px;">
            Macro factor graph · unified aggregation · market structure · strategy lab ·
            institutional research · MCP bridge · integrity
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    macro_block = (matrix or {}).get("macro") if isinstance(matrix, dict) else None
    if isinstance(macro_block, dict) and macro_block.get("regime"):
        st.caption(
            f"Live matrix macro overlay: {macro_block.get('regime')} · "
            f"{macro_block.get('confidence', 0):.0%} confidence · "
            f"applied={macro_block.get('apply')}"
        )

    tabs = st.tabs([
        "MACRO FACTOR GRAPH", "UNIFIED AGGREGATION", "MARKET STRUCTURE",
        "STRATEGY LAB", "INSTITUTIONAL RESEARCH", "MCP / REA", "INTEGRITY",
    ])
    with tabs[0]:
        _render_macro()
    with tabs[1]:
        _render_aggregation()
    with tabs[2]:
        _render_structure()
    with tabs[3]:
        _render_strategy_lab()
    with tabs[4]:
        _render_research()
    with tabs[5]:
        _render_mcp()
    with tabs[6]:
        _render_integrity()
