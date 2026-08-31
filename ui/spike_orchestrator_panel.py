"""ZERO // BIOLOGICAL SPIKE ORCHESTRATOR — SPIKE CORE tab console

On-demand cold-boot console for the sparse execution network. Matches the
digital-core theme (gold titles, digital-cards, Orbitron HUD chips).

``core.spike_orchestrator`` is imported lazily. Missing backend, empty audit,
or a failed spike must never crash the rest of the terminal.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parents[1]
_FALLBACK_AUDIT = _ROOT / "db" / "spike_execution_audit.json"

_RED = "#E50914"
_GOLD = "#D4AF37"
_GREEN = "#00E676"

_DEFAULT_RAG_QUERY = "Market trend forecast analysis verification"
_DEFAULT_PRED_CLOSE = 24500.0
_DEFAULT_ACTUAL_CLOSE = 24100.0
_DEFAULT_VIX = 18.5
_DEFAULT_LATENCY_MS = 42.0

_AUDIT_COLUMNS = (
    ("timestamp", "TIME (UTC)"),
    ("system_node_key", "NODE"),
    ("execution_method_name", "METHOD"),
    ("ok", "OK"),
    ("elapsed_ms", "MS"),
    ("class_name", "CLASS"),
    ("error", "ERROR"),
    ("result_preview", "RESULT"),
)


def _get_orchestrator_cls():
    try:
        from core.spike_orchestrator import BiologicalSpikeOrchestrator
        return BiologicalSpikeOrchestrator
    except Exception:
        return None


def _audit_path(orchestrator: Any = None) -> Path:
    if orchestrator is not None:
        path = getattr(orchestrator, "audit_path", None)
        if path:
            return Path(path)
    try:
        from core.spike_orchestrator import default_audit_path
        return Path(default_audit_path())
    except Exception:
        return _FALLBACK_AUDIT


def _session_orchestrator() -> Tuple[Any, Optional[str]]:
    """Lazy session singleton. Import/init failures become a UI message."""
    cached = st.session_state.get("spike_orchestrator")
    err = st.session_state.get("spike_orchestrator_error")
    if cached is not None or err:
        return cached, err
    cls = _get_orchestrator_cls()
    if cls is None:
        msg = (
            "BiologicalSpikeOrchestrator is unavailable "
            "(core.spike_orchestrator failed to import). "
            "The console stays browsable; spike buttons stay disabled."
        )
        st.session_state["spike_orchestrator"] = None
        st.session_state["spike_orchestrator_error"] = msg
        return None, msg
    try:
        st.session_state["spike_orchestrator"] = cls()
        st.session_state["spike_orchestrator_error"] = None
    except Exception as exc:
        st.session_state["spike_orchestrator"] = None
        st.session_state["spike_orchestrator_error"] = f"Spike orchestrator init failed: {exc}"
    return st.session_state.get("spike_orchestrator"), st.session_state.get("spike_orchestrator_error")


def _load_audit_records(path: Path) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    if not path.is_file():
        return [], None
    try:
        if path.stat().st_size <= 0:
            return [], None
    except OSError as exc:
        return [], f"Audit file unreadable: {exc}"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        return [], f"Audit log is corrupted or unreadable: {exc}"
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)], None
    if isinstance(raw, dict):
        return [raw], None
    return [], "Audit log is not a JSON list."


def _audit_frame(records: List[Dict[str, Any]]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for rec in reversed(records[-40:]):
        row: Dict[str, Any] = {}
        for key, label in _AUDIT_COLUMNS:
            value = rec.get(key)
            if key == "ok":
                row[label] = "YES" if value else "NO"
            elif key == "result_preview":
                if value is None:
                    row[label] = "—"
                elif isinstance(value, (list, dict)):
                    row[label] = json.dumps(value, ensure_ascii=False, default=str)[:240]
                else:
                    text = str(value)
                    row[label] = text if len(text) <= 240 else text[:237] + "…"
            elif value in (None, ""):
                row[label] = "—"
            else:
                row[label] = value
        rows.append(row)
    return pd.DataFrame(rows)


def _format_result_payload(payload: Any) -> Tuple[str, str]:
    """Return (tone, text) for a spike return dict or raw subsystem output."""
    if isinstance(payload, dict) and "ok" in payload:
        elapsed = payload.get("elapsed_ms")
        suffix = f"  ·  {elapsed:g} ms" if isinstance(elapsed, (int, float)) else ""
        if not payload.get("ok"):
            err = payload.get("error") or "Spike failed."
            return "error", f"{err}{suffix}"
        tone, text = _format_result_payload(payload.get("result"))
        return tone, f"{text}{suffix}"

    if isinstance(payload, tuple) and len(payload) == 2:
        tripped, message = payload
        text = str(message) if message is not None else ""
        if tripped:
            return "error", text or "HOLD — situational tackle tripped."
        return "success", text or "Vitals clear."

    if payload is None:
        return "info", "Spike completed with an empty result."
    return "info", str(payload)


def _show_result(tone: str, text: str, *, key: str) -> None:
    if len(text) > 400:
        if tone == "error":
            st.error("Spike failed — full payload below.")
        elif tone == "success":
            st.success("Spike completed — full payload below.")
        st.text_area("Spike output", value=text, height=160, disabled=True, key=key)
        return
    if tone == "error":
        st.error(text)
    elif tone == "success":
        st.success(text)
    elif tone == "warning":
        st.warning(text)
    else:
        st.info(text)


def _fire_spike(
    orchestrator: Any,
    node: str,
    method: str,
    *args: Any,
    spinner: str,
    result_key: str,
) -> None:
    with st.spinner(spinner):
        try:
            payload = orchestrator.trigger_spike_activation(node, method, *args)
        except Exception as exc:
            payload = {
                "ok": False,
                "error": f"Spike call raised: {exc}",
                "result": None,
                "elapsed_ms": None,
            }
    st.session_state[result_key] = payload
    st.rerun()


def _metric_card(label: str, value: str, note: str, accent: str) -> str:
    safe_label = html.escape(label)
    safe_value = html.escape(value)
    safe_note = html.escape(note)
    safe_accent = html.escape(accent)
    return (
        f"<div class='digital-card' style='text-align:center;border-left:3px solid {safe_accent};'>"
        f"<p class='label-grey'>{safe_label}</p>"
        f"<p style='color:{safe_accent};font-weight:900;font-size:1.55rem;margin:4px 0;"
        f"font-family:Orbitron,sans-serif;letter-spacing:1px;'>{safe_value}</p>"
        f"<p class='label-grey' style='font-size:0.58rem;'>{safe_note}</p>"
        f"</div>"
    )


def render_spike_orchestrator_panel() -> None:
    """SPIKE CORE tab: floodlight vs biological metrics, 3 spike buttons, audit."""
    try:
        from ui.terminal_chrome import render_section_header
        render_section_header("SK", "BIOLOGICAL SPIKE ORCHESTRATOR CORE")
    except Exception:
        st.markdown(
            "<h2 class='gold-title'>BIOLOGICAL SPIKE ORCHESTRATOR CORE</h2>",
            unsafe_allow_html=True,
        )

    st.markdown(
        "<p class='label-grey' style='margin-bottom:18px;'>"
        "Sparse event network — subsystems stay cold on disk until a spike. "
        "One method fires, then the module is flushed. "
        "This is the on-demand path; AEGIS QUANT keeps the always-on consoles."
        "</p>",
        unsafe_allow_html=True,
    )

    orchestrator, orch_err = _session_orchestrator()
    online = orchestrator is not None
    state = "ONLINE" if online else "OFFLINE"
    state_color = _GREEN if online else _RED
    st.markdown(
        f"<div style='display:inline-flex;align-items:center;gap:10px;"
        f"background:rgba(0,0,0,0.45);border:1px solid {state_color}55;border-radius:6px;"
        f"padding:8px 14px;margin:2px 0 14px 0;'>"
        f"<span style='width:9px;height:9px;border-radius:50%;background:{state_color};"
        f"box-shadow:0 0 8px {state_color};display:inline-block;'></span>"
        f"<span style=\"font-family:'Orbitron',sans-serif;font-size:0.68rem;font-weight:900;"
        f"letter-spacing:2px;color:{state_color};\">SPIKE NETWORK {state}</span>"
        f"<span style='font-size:0.6rem;color:#888;letter-spacing:1px;'>"
        f"{'COLD BOOT · ZERO BACKGROUND DRAG' if online else 'ORCHESTRATOR UNAVAILABLE'}"
        f"</span></div>",
        unsafe_allow_html=True,
    )
    if orch_err:
        st.error(orch_err)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            _metric_card(
                "FLOODLIGHT FOOTPRINT",
                "700 W",
                "+35× always-on silicon draw",
                _RED,
            ),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            _metric_card(
                "BIOLOGICAL TARGET",
                "20 W",
                "Sparse spike-driven benchmark",
                _GOLD,
            ),
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            _metric_card(
                "ON-DEMAND CORE",
                "ACTIVE",
                "Zero background static drag",
                _GREEN,
            ),
            unsafe_allow_html=True,
        )

    st.markdown("<div style='margin-bottom:18px;'></div>", unsafe_allow_html=True)
    try:
        from ui.terminal_chrome import render_control_deck_divider
        render_control_deck_divider("TRIGGER SPARSE SUBSYSTEM ACTIONS")
    except Exception:
        st.markdown("<p class='gold-title' style='font-size:0.85rem;'>TRIGGER SPARSE SUBSYSTEM ACTIONS</p>", unsafe_allow_html=True)

    col_x, col_y, col_z = st.columns(3)
    disabled = not online

    with col_x:
        st.markdown(
            "<p class='gold-title' style='font-size:0.8rem;'>MONITORING</p>",
            unsafe_allow_html=True,
        )
        st.markdown("<p class='label-grey'>OKF local semantic retrieval</p>", unsafe_allow_html=True)
        rag_query = st.text_input(
            "Retrieval query",
            value=_DEFAULT_RAG_QUERY,
            key="spike_rag_query",
            disabled=disabled,
        )
        if st.button("RUN LOCAL NEWS RAG", key="spike_rag_btn", width="stretch", disabled=disabled):
            _fire_spike(
                orchestrator,
                "monitoring",
                "local_semantic_retrieval",
                (rag_query or "").strip() or _DEFAULT_RAG_QUERY,
                spinner="Cold-booting semantic indexing modules…",
                result_key="spike_last_monitoring",
            )
        last_mon = st.session_state.get("spike_last_monitoring")
        if last_mon is not None:
            tone, text = _format_result_payload(last_mon)
            _show_result(tone, text, key="spike_out_monitoring")

    with col_y:
        st.markdown(
            "<p class='gold-title' style='font-size:0.8rem;'>DECISION ENGINE</p>",
            unsafe_allow_html=True,
        )
        st.markdown("<p class='label-grey'>Self-reflection learning loop</p>", unsafe_allow_html=True)
        pred_close = st.number_input(
            "Predicted close",
            value=float(_DEFAULT_PRED_CLOSE),
            step=10.0,
            key="spike_pred_close",
            disabled=disabled,
        )
        actual_close = st.number_input(
            "Actual close",
            value=float(_DEFAULT_ACTUAL_CLOSE),
            step=10.0,
            key="spike_actual_close",
            disabled=disabled,
        )
        if st.button("EVALUATE TARGET VARIANCE", key="spike_learn_btn", width="stretch", disabled=disabled):
            _fire_spike(
                orchestrator,
                "decision_engine",
                "execute_self_reflection_learning",
                float(pred_close),
                float(actual_close),
                spinner="Cold-booting neural weight structures…",
                result_key="spike_last_decision",
            )
        last_dec = st.session_state.get("spike_last_decision")
        if last_dec is not None:
            tone, text = _format_result_payload(last_dec)
            _show_result(tone, text, key="spike_out_decision")

    with col_z:
        st.markdown(
            "<p class='gold-title' style='font-size:0.8rem;'>RISK MITIGATION</p>",
            unsafe_allow_html=True,
        )
        st.markdown("<p class='label-grey'>Situational market vitals</p>", unsafe_allow_html=True)
        vix = st.number_input(
            "India VIX",
            value=float(_DEFAULT_VIX),
            step=0.1,
            key="spike_vix",
            disabled=disabled,
        )
        latency = st.number_input(
            "Server latency (ms)",
            value=float(_DEFAULT_LATENCY_MS),
            step=1.0,
            key="spike_latency",
            disabled=disabled,
        )
        if st.button("RUN VITALS DIAGNOSTIC", key="spike_vitals_btn", width="stretch", disabled=disabled):
            _fire_spike(
                orchestrator,
                "risk_mitigation",
                "inspect_market_vitals",
                float(vix),
                float(latency),
                spinner="Cold-booting safety intercept modules…",
                result_key="spike_last_risk",
            )
        last_risk = st.session_state.get("spike_last_risk")
        if last_risk is not None:
            tone, text = _format_result_payload(last_risk)
            _show_result(tone, text, key="spike_out_risk")

    try:
        from ui.terminal_chrome import render_control_deck_divider
        render_control_deck_divider("LOCAL SYSTEM AUDIT AND ESCALATION LOGS")
    except Exception:
        st.markdown(
            "<p class='gold-title' style='font-size:0.85rem;'>LOCAL SYSTEM AUDIT AND ESCALATION LOGS</p>",
            unsafe_allow_html=True,
        )

    audit_path = _audit_path(orchestrator)
    records, audit_err = _load_audit_records(audit_path)
    st.caption(f"Audit · {audit_path}")
    if audit_err:
        st.warning(audit_err)
    elif not records:
        st.info("System tracking clean. No background activation calls registered yet.")
    else:
        frame = _audit_frame(records)
        if frame.empty:
            st.info("System tracking clean. No background activation calls registered yet.")
        else:
            st.dataframe(frame, hide_index=True, width="stretch")
            ok_n = sum(1 for rec in records if rec.get("ok"))
            fail_n = len(records) - ok_n
            st.caption(
                f"{len(records)} spike event(s) · {ok_n} ok · {fail_n} failed · newest first"
            )
