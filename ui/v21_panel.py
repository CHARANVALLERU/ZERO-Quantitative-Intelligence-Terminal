"""
ZERO v2.1 panel — Suggestions · Trading · Research · Agentic AI · Integrations · Forecast.

Presentation only. Every number comes from ``engine.v21`` and every network call
happens behind a button, so opening the tab is instant and never blocks the
terminal.

Layout contract (shared by all v2.1 tabs):
  * index selector pinned top-right,
  * one card style, one palette,
  * sources and delays printed under the data they describe,
  * "unavailable" is shown as a reason, never as a zero.
"""
from __future__ import annotations

import html
import time
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from engine.v21 import agents as agents_mod
from engine.v21 import (desks, ecosystem, forecast, gex, indices, integrations, quant,
                        reports, signals, trading)

C = {
    "bg": "#050505",
    "panel": "#0c0c0c",
    "line": "#1b1b1b",
    "gold": "#D4AF37",
    "red": "#E50914",
    "green": "#00E676",
    "amber": "#FFD600",
    "mute": "#6b6b6b",
    "text": "#e8e8e8",
    "blue": "#2E9BFF",
}

_STATUS_COLOR = {"live": C["green"], "partial": C["amber"], "link": C["blue"],
                 "external": C["red"], "planned": C["mute"]}


# ── shared bits ────────────────────────────────────────────────────────────

def _css() -> None:
    st.markdown(
        f"""
        <style>
        .v21-card {{background:{C['panel']};border:1px solid {C['line']};
                    border-left:3px solid var(--a,{C['gold']});border-radius:8px;
                    padding:12px 14px;margin-bottom:10px;}}
        .v21-card h4 {{margin:0 0 6px 0;font-size:0.95rem;color:{C['text']};
                       letter-spacing:.04em;text-transform:uppercase;}}
        .v21-k {{color:{C['mute']};font-size:0.72rem;text-transform:uppercase;
                 letter-spacing:.12em;}}
        .v21-v {{color:{C['text']};font-size:1.15rem;font-weight:600;}}
        .v21-badge {{display:inline-block;padding:1px 7px;border-radius:9px;
                     font-size:0.66rem;letter-spacing:.1em;text-transform:uppercase;}}
        .v21-tile {{border-radius:6px;padding:8px;margin:3px;color:#fff;font-size:0.72rem;}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _badge(text: str, color: str) -> str:
    return f"<span class='v21-badge' style='background:{color}22;color:{color};border:1px solid {color}55'>{html.escape(text)}</span>"


def _card(title: str, body: str, accent: str = C["gold"], badge: str = "") -> None:
    st.markdown(
        f"<div class='v21-card' style='--a:{accent}'><h4>{html.escape(title)} "
        f"{_badge(badge, accent) if badge else ''}</h4>{body}</div>",
        unsafe_allow_html=True,
    )


def _unavailable(reason: str) -> None:
    st.markdown(f"<div class='v21-card' style='--a:{C['red']}'>"
                f"<span class='v21-k'>unavailable</span><br>{html.escape(str(reason))}</div>",
                unsafe_allow_html=True)


def _kv(pairs: List[tuple]) -> str:
    cells = "".join(
        f"<div style='display:inline-block;margin-right:18px'>"
        f"<div class='v21-k'>{html.escape(str(k))}</div>"
        f"<div class='v21-v'>{html.escape(str(v))}</div></div>"
        for k, v in pairs
    )
    return f"<div style='margin:4px 0'>{cells}</div>"


def _index_selector() -> str:
    """
    Index selector (top-right) + the **Run all suggestions** trigger beside it.

    The button only records the selected index as pending; the Suggestions tab
    consumes that flag on the same rerun. Nothing is computed here, so switching
    index never blocks the rest of the panel.
    """
    left, right = st.columns([3, 2])
    with left:
        st.markdown(
            f"<div style='font-size:1.35rem;font-weight:700;color:{C['text']};"
            f"letter-spacing:.08em'>ZERO <span style='color:{C['gold']}'>v2.1</span></div>",
            unsafe_allow_html=True,
        )
    with right:
        pick, act = st.columns([2, 1])
        with pick:
            chosen = st.selectbox("Index", indices.index_options(), index=0,
                                  key="v21_index", label_visibility="collapsed")
        key = indices.canonical(chosen)
        with act:
            if st.button("⚡ Run all", key="v21_run_all_btn", width="stretch",
                         help="Run every suggestion engine for this index and "
                              "build one labelled trade call"):
                st.session_state["v21_pending"] = key
    return key


def _quote_strip(key: str) -> None:
    q = indices.quote(key)
    if not q.get("available"):
        _unavailable(q.get("reason", "no quote"))
        return
    up = (q.get("change") or 0) >= 0
    colour = C["green"] if up else C["red"]
    last_txt = f"{q['last']:,.2f}"
    chg_txt = f"{q.get('change_pct') or 0:+.2f}%"
    body = _kv([("index", q["label"]), ("last", last_txt), ("change", chg_txt),
                ("source", q.get("source") or "-")])
    st.markdown(f"<div class='v21-card' style='--a:{colour}'>{body}</div>",
                unsafe_allow_html=True)


# ── Suggestions ────────────────────────────────────────────────────────────

def _collect_ideas(key: str) -> List[Dict[str, Any]]:
    """Run every suggestion engine for one index and return their ideas."""
    ideas: List[Dict[str, Any]] = []

    ret = trading.index_returns(key, bars=400)
    peer = "BANKNIFTY" if key != "BANKNIFTY" else "NIFTY"
    peer_ret = trading.index_returns(peer, bars=400)
    if ret is not None and peer_ret is not None:
        n = min(len(ret), len(peer_ret))
        h = signals.hedge_regression(peer_ret[-n:], ret[-n:])
        if h.get("available"):
            h["label"] = f"Hedge vs {peer} · {h['label']}"
            ideas.append(h)
        a = pd.Series(ret[-n:]).cumsum().to_numpy()
        b = pd.Series(peer_ret[-n:]).cumsum().to_numpy()
        s = signals.stat_arb(a, b, lookback=200, name_a=indices.canonical(key), name_b=peer)
        if s.get("available"):
            ideas.append(s)

    try:
        from engine.aite.exam import load_market_frame
        df = load_market_frame(indices.aite_symbol(key), bars=300)
        pat = signals.chart_context(df)
        if pat.get("available"):
            ideas.append(pat)
    except Exception:
        pass

    try:
        g = gex.live(key)
        if g.get("available"):
            sg = g["suggestion"]
            ideas.append({
                "engine": "gex", "label": sg["label"], "bias": sg["bias"],
                "confidence": sg["confidence"], "detail": sg["detail"],
                "metrics": {"gamma_flip": g.get("gamma_flip"), "regime": g["regime"],
                            "net_gex_bn": g["totals"]["net_gex_bn"]},
                "invalidation": sg["invalidation"],
            })
    except Exception:
        pass

    return ideas


def _render_ideas(ideas: List[Dict[str, Any]]) -> None:
    """Render idea cards, ranked by confidence."""
    if not ideas:
        _unavailable("no engine produced a signal for this index")
        return
    for idea in sorted(ideas, key=lambda x: x.get("confidence", 0), reverse=True):
        bias = str(idea.get("bias", "")).upper()
        accent = (C["green"] if bias.startswith(("BUY", "LONG")) else
                  C["red"] if bias.startswith(("SELL", "SHORT")) else C["gold"])
        metrics = " · ".join(f"{k}={v}" for k, v in (idea.get("metrics") or {}).items())
        _card(idea.get("label", idea["engine"]),
              idea.get("detail", "") + f"<div class='v21-k' style='margin-top:6px'>{html.escape(metrics)}</div>",
              accent=accent, badge=bias)
        zseries = (idea.get("chart") or {}).get("z")
        if zseries:
            st.line_chart(pd.DataFrame({"z": zseries}))


def _consensus_card(key: str, ideas: List[Dict[str, Any]],
                    eco: Optional[Dict[str, Any]] = None) -> None:
    """Headline: every engine's vote rolled into one labelled trade call."""
    pool = list(ideas)
    sg = (eco or {}).get("suggestion") or {}
    if sg.get("available"):
        pool.append({"engine": "ecosystem", "label": sg.get("label", "ecosystem"),
                     "bias": sg.get("bias"), "confidence": sg.get("confidence", 0),
                     "detail": sg.get("detail", ""), "available": True})
    cons = signals.consensus(pool)
    if not cons.get("available"):
        _unavailable(cons.get("reason"))
        return
    colour = {"LONG": C["green"], "SHORT": C["red"]}.get(cons["bias"], C["amber"])
    body = cons["detail"] + _kv([("action", cons.get("action", cons["bias"])),
                                 ("confidence", cons["confidence"]),
                                 ("long / short / flat", f"{cons['longs']}/{cons['shorts']}/{cons['neutral']}"),
                                 ("gex", cons.get("gex_label") or "-")])
    _card(f"{cons['label']} · {indices.label(key)}", body,
          accent=colour, badge=cons["bias"])
    st.caption(f"invalidation: {cons['invalidation']}")
    with st.expander("engine votes"):
        st.dataframe(pd.DataFrame(cons["votes"]), hide_index=True, width="stretch")


def _eco_suggestion(key: str, result: Optional[Dict[str, Any]] = None,
                    show_button: bool = True) -> None:
    if show_button and st.button("Run trading ecosystem", key="v21_eco_run"):
        with st.spinner("breeding → backtesting → regime → arbitrage → risk → builder"):
            st.session_state["v21_eco"] = ecosystem.run_cycle(key, n_genomes=6)
    out = result if result is not None else st.session_state.get("v21_eco")
    if not out:
        st.caption("No cycle run in this session yet.")
        return
    if not out.get("available"):
        _unavailable(out.get("reason"))
        return
    stages = out["stages"]
    st.dataframe(pd.DataFrame([{k: s[k] for k in ("stage", "status", "detail", "ms")} for s in stages]),
                 hide_index=True, width="stretch")
    sg = out.get("suggestion") or {}
    if sg.get("available"):
        lv = sg.get("levels") or {}
        _card(sg["label"], sg["detail"] + _kv([("entry", lv.get("entry")), ("stop", lv.get("stop")),
                                               ("target", lv.get("target")), ("conf", sg.get("confidence"))]),
              accent=C["gold"], badge=str(sg.get("bias")))
    if out.get("strategy_spec"):
        st.caption(f"strategy spec → {out['strategy_spec']}")


def _run_all(key: str) -> Dict[str, Any]:
    """Every suggestion engine for one index, plus a full ecosystem cycle."""
    with st.spinner(f"running every engine on {indices.label(key)} — GEX, hedge, "
                    f"stat-arb, pattern, ecosystem"):
        return {"ideas": _collect_ideas(key),
                "eco": ecosystem.run_cycle(key, n_genomes=6),
                "at": time.strftime("%H:%M:%S")}


def _tab_suggestions(key: str) -> None:
    """
    One click, every engine.

    Results live in one dict keyed by index, so a run survives Streamlit reruns
    and switching index can never show another index's ideas.
    """
    _quote_strip(key)
    runs = st.session_state.setdefault("v21_runs", {})
    manual = st.button("Build suggestions", key="v21_sugg_build")
    if st.session_state.pop("v21_pending", None) == key or manual:
        runs[key] = _run_all(key)

    payload = runs.get(key)
    if payload:
        st.caption(f"all-engines run at {payload['at']} · index {indices.label(key)}")
        _consensus_card(key, payload["ideas"], payload.get("eco"))
        st.divider()
        _render_ideas(payload["ideas"])
        st.divider()
        _eco_suggestion(key, result=payload.get("eco"), show_button=False)
    else:
        st.caption("Press **⚡ Run all** (top right) or **Build suggestions** — engines only run on demand.")


# ── Trading ────────────────────────────────────────────────────────────────

def _gex_chart(g: Dict[str, Any]) -> None:
    rows = gex.hedge_ladder(g, top=12)
    if not rows:
        return
    df = pd.DataFrame(rows)[["strike", "call_gex", "put_gex", "net_gex"]].set_index("strike")
    st.caption("dealer hedge by strike — green call wall, red put wall (₹ notional per 1% move)")
    st.bar_chart(df[["call_gex", "put_gex"]], color=[C["green"], C["red"]])
    st.caption(f"net GEX {g['totals']['net_gex_bn']} bn · flip {g.get('gamma_flip')} · "
               f"regime {g['regime']} · spot vs flip {g.get('spot_vs_flip')}")


def _tab_trading(key: str) -> None:
    _quote_strip(key)
    cols = st.columns([1, 1])
    with cols[0]:
        if st.button("Refresh F&O board", key="v21_fno_btn"):
            st.session_state["v21_fno"] = trading.fno_board(key)
        board = st.session_state.get("v21_fno")
        if board:
            g = board.get("gex") or {}
            if g.get("available"):
                _card(f"GEX · {g['regime']}", html.escape(g["playbook"]),
                      accent=C["green"] if g["regime"] == "positive" else C["red"],
                      badge=g["regime"])
                _gex_chart(g)
            chain = board.get("chain") or {}
            if chain and not chain.get("error"):
                _card("Option chain", _kv([("PCR", chain.get("pcr")), ("max pain", chain.get("max_pain")),
                                           ("expiry", chain.get("nearest_expiry")),
                                           ("call wall", chain.get("max_ce_oi_strike")),
                                           ("put wall", chain.get("max_pe_oi_strike"))]))
            else:
                _unavailable((chain or {}).get("error", "no chain"))
            st.caption(f"{board.get('source')} · {board.get('delay')}")
        else:
            st.caption("Press **Refresh F&O board** for the option chain and GEX.")
    with cols[1]:
        if st.button("Load option ladder", key="v21_opt_btn"):
            st.session_state["v21_opt"] = trading.option_table(key)
        rows = st.session_state.get("v21_opt")
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.caption("Strikes around the money, CE and PE side by side.")

    st.divider()
    left, right = st.columns(2)
    with left:
        if st.button("Commodities tape", key="v21_comm_btn"):
            st.session_state["v21_comm"] = trading.commodities()
        cm = st.session_state.get("v21_comm")
        if cm:
            if cm.get("available"):
                st.dataframe(pd.DataFrame(cm["rows"])[["name", "last", "change_pct", "unit"]],
                             hide_index=True, width="stretch")
                with st.expander("Physical chain: mine → ship → port → mill"):
                    for s in cm["supply_chain"]:
                        st.markdown(f"**{s['stage']}** — {s['what']}  \n"
                                    f"watch: {s['watch']}  \n[{s['source']}]({s['url']})")
                st.caption(f"{cm['source']} · {cm['delay']}")
            else:
                _unavailable(cm.get("error") or "no commodity quotes")
    with right:
        if st.button("Sector heatmap", key="v21_heat_btn"):
            st.session_state["v21_heat"] = trading.heatmap(key)
        hm = st.session_state.get("v21_heat")
        if hm:
            if hm.get("available"):
                tiles = "".join(
                    f"<div class='v21-tile' style='background:{r['color']};display:inline-block;"
                    f"width:{max(90, r['w']*13):.0f}px'>"
                    f"<b>{html.escape(r['name'])}</b><br>{r['change_pct']:+.2f}%</div>"
                    for r in hm["rows"]
                )
                st.markdown(f"<div style='line-height:1.1'>{tiles}</div>", unsafe_allow_html=True)
                st.dataframe(pd.DataFrame(hm["sectors"])[["sector", "change_pct", "weight"]],
                             hide_index=True, width="stretch")
                b = hm["breadth"]
                st.caption(f"breadth {b['advancing']}/{b['total']} advancing · {hm['source']}")
            else:
                _unavailable(hm.get("error") or "no heatmap data")


# ── Research ───────────────────────────────────────────────────────────────

def _tab_research(key: str) -> None:
    _quote_strip(key)
    st.markdown("#### SSRN lanes")
    cols = st.columns(3)
    for i, topic in enumerate(reports.SSRN_TOPICS):
        with cols[i % 3]:
            st.link_button(topic["label"], topic["url"], width="stretch")
    st.caption("Deep links into SSRN search; no scraping, no paywalled PDFs.")

    st.markdown("#### Course quant (MIT / Stanford)")
    for c in reports.COURSES:
        st.markdown(f"- [{c['label']}]({c['url']}) — {c['covers']}")

    with st.expander("Bond pricing & duration", expanded=False):
        face = st.number_input("Face", value=1000.0, step=100.0, key="v21_b_face")
        cpn = st.number_input("Coupon %", value=7.0, step=0.25, key="v21_b_cpn")
        yrs = st.number_input("Years", value=10.0, step=1.0, key="v21_b_yrs")
        ytm = st.number_input("YTM %", value=7.0, step=0.1, key="v21_b_ytm")
        d = quant.bond_duration(face, cpn / 100, yrs, ytm / 100)
        _card("Bond", _kv([("price", f"{d['price']:,.2f}"), ("macaulay", f"{d['macaulay']:.2f}"),
                           ("modified", f"{d['modified']:.2f}"), ("convexity", f"{d['convexity']:.1f}")]))

    with st.expander("Mean-variance frontier", expanded=False):
        if st.button("Build frontier", key="v21_mv"):
            rets = trading.index_returns(key, bars=400)
            if rets is None:
                _unavailable("no returns for this index")
            else:
                mu = [float(rets.mean()) * 252, 0.11, 0.06]
                vol = float(rets.std(ddof=1))
                cov = [[vol**2, vol * 0.18 * 0.8, vol * 0.12 * 0.3],
                       [vol * 0.18 * 0.8, 0.18**2, 0.18 * 0.12 * 0.4],
                       [vol * 0.12 * 0.3, 0.18 * 0.12 * 0.4, 0.12**2]]
                mv = quant.mean_variance_frontier(mu, cov)
                if mv.get("available"):
                    st.line_chart(pd.DataFrame(mv["frontier"]).set_index("vol"))
                    _card("Tangency", _kv([("ret", mv["tangency"]["ret"]),
                                            ("vol", mv["tangency"]["vol"]),
                                            ("sharpe", mv["tangency"]["sharpe"])]))

    st.divider()
    st.markdown("#### Reports")
    cols = st.columns([1, 1, 2])
    with cols[0]:
        if st.button("Generate report", key="v21_rep", width="stretch"):
            with st.spinner("collecting sections"):
                r = reports.build_research_report(key)
            st.session_state["v21_rep_path"] = r.get("path")
            st.success(f"wrote {r.get('path')}")
    with cols[1]:
        st.link_button("OpenBB", "https://github.com/OpenBB-finance/OpenBB", width="stretch")
        st.link_button("Alpaca", "https://alpaca.markets/", width="stretch")
    with cols[2]:
        reps = reports.list_reports(10)
        if reps:
            st.dataframe(pd.DataFrame(reps)[["name", "bytes", "mtime"]], hide_index=True, width="stretch")
        else:
            st.caption("REPORTS/ is empty — generate the first report.")


# ── Agentic AI ─────────────────────────────────────────────────────────────

def _tab_agentic(key: str) -> None:
    _quote_strip(key)
    st.markdown("#### Trading ecosystem")
    _eco_suggestion(key)

    st.divider()
    st.markdown("#### Specialist desk · item 19")
    desk_out = _desk("agent desk", desks.agent_desk, index=key)
    if _desk_ready(desk_out):
        _card(desk_out["label"], desk_out["detail"], accent=C["blue"],
              badge=desk_out["bias"])
        st.dataframe(pd.DataFrame(desk_out["agents"])[["agent", "bias", "confidence", "detail"]],
                     hide_index=True, width="stretch")
        st.caption("LLM prose: " + ("on" if desk_out["llm_prose"] else "off (no OPENAI_API_KEY)")
                   + " · " + desk_out["note"])

    st.divider()
    st.markdown("#### SEC agents")
    ticker = st.text_input("Ticker (SEC EDGAR)", value="AAPL", key="v21_ticker")
    c1, c2, c3 = st.columns(3)
    if c1.button("Bull vs bear", key="v21_debate_btn", width="stretch"):
        with st.spinner("reading filings"):
            st.session_state["v21_debate"] = agents_mod.debate(ticker)
    if c2.button("Insider clusters", key="v21_insider_btn", width="stretch"):
        st.session_state["v21_insider"] = agents_mod.insider_clusters(ticker)
    if c3.button("Earnings stress", key="v21_stress_btn", width="stretch"):
        st.session_state["v21_stress"] = agents_mod.earnings_stress(ticker)

    d = st.session_state.get("v21_debate")
    if d:
        if d.get("available"):
            _card(f"Debate · {d['ticker']}", d["judge"]["verdict"],
                  accent=C["green"] if d["judge"]["winner"] == "BULL" else C["red"],
                  badge=d["judge"]["winner"])
            col1, col2 = st.columns(2)
            col1.markdown("**BULL**\n" + "\n".join(f"- {p}" for p in d["bull"]["points"] or ["—"]))
            col2.markdown("**BEAR**\n" + "\n".join(f"- {p}" for p in d["bear"]["points"] or ["—"]))
        else:
            _unavailable(d.get("reason"))

    i = st.session_state.get("v21_insider")
    if i:
        if i.get("available"):
            _card(i["label"], f"{i['filings_in_window']} Form 4 filings in {i['window_days']} days. "
                              f"parsed={i['transactions']['parsed']} buys={i['transactions']['purchases']} "
                              f"sells={i['transactions']['sales']}",
                  accent=C["amber"] if i.get("cluster") else C["mute"],
                  badge="CLUSTER" if i.get("cluster") else "quiet")
        else:
            _unavailable(i.get("reason"))

    s = st.session_state.get("v21_stress")
    if s:
        if s.get("available"):
            _card(s["label"], s["detail"], accent=C["gold"],
                  badge="predictive" if s["predictive"] else "no signal")
            st.dataframe(pd.DataFrame(s["events"]), hide_index=True, width="stretch")
        else:
            _unavailable(s.get("reason"))


# ── Integrations ───────────────────────────────────────────────────────────

def _tab_integrations(key: str) -> None:
    s = integrations.summary()
    st.markdown(
        _kv([("items", f"{s['items']}/{s['total']}"), ("live", s["counts"]["live"]),
             ("partial", s["counts"]["partial"]), ("link", s["counts"]["link"]),
             ("external", s["counts"]["external"])]),
        unsafe_allow_html=True,
    )
    st.caption("Every requested item, its honest status and its action. "
               "'external' means the target app must be extracted/installed first.")

    rows = integrations.all_integrations()
    panels = sorted({r["panel"] for r in rows})
    chosen = st.multiselect("Filter by panel", panels, default=panels, key="v21_int_filter")
    for r in rows:
        if r["panel"] not in chosen:
            continue
        colour = _STATUS_COLOR.get(r["status"], C["mute"])
        act = integrations.resolve_action(r["action"])
        with st.container(border=True):
            head, tail = st.columns([4, 1])
            head.markdown(f"**{r['label']}** &nbsp; {_badge(r['status'], colour)} "
                          f"<span class='v21-k'>#{r['item']} · {r['panel']}</span>",
                          unsafe_allow_html=True)
            head.caption(r["detail"] + (f" — _gap: {r['gap']}_" if r.get("gap") else ""))
            if act["kind"] == "url":
                tail.link_button("Open", act["url"], width="stretch")
            elif act["kind"] == "tab":
                tail.caption(f"tab: {act['tab']}")
            elif act["kind"] == "callable":
                tail.caption("engine ready" if act["ok"] else f"missing: {act.get('reason','')[:40]}")
            else:
                tail.caption("needs REA")


# ── Forecast ───────────────────────────────────────────────────────────────

def _tab_forecast(key: str) -> None:
    st.caption("Existing forecast panel stays as-is. These are the six named models: "
               "each button runs exactly one and charts only its own output.")
    for m in forecast.status():
        with st.container(border=True):
            left, right = st.columns([4, 1])
            left.markdown(f"**{m['name']}** {_badge('ready' if m['available'] else 'not installed', C['green'] if m['available'] else C['red'])}")
            left.caption(m["note"] + f" · [{m['repo']}]({m['repo']})")
            if right.button("Run", key=f"v21_fc_{m['key']}", width="stretch"):
                with st.spinner(f"{m['name']} forecasting"):
                    st.session_state[f"v21_fc_res_{m['key']}"] = forecast.run(m["key"], key)
            res = st.session_state.get(f"v21_fc_res_{m['key']}")
            if res:
                if res.get("available"):
                    ch = res["chart"]
                    hist = ch["history"] + ch["prediction"]
                    st.line_chart(pd.DataFrame({"close": hist}))
                    st.caption(f"{res['model']} · {res['seconds']}s · horizon {res['horizon']}")
                else:
                    _unavailable(res.get("reason"))


# ── Desks (items 7–10, 23, 24) ──────────────────────────────────────────────

def _desk(name: str, fn, **kwargs) -> Optional[Dict[str, Any]]:
    """Run one desk on a button and keep its result for the session."""
    if st.button(f"Load {name}", key=f"v21_desk_btn_{name}"):
        with st.spinner(name):
            st.session_state[f"v21_desk_{name}"] = fn(**kwargs)
    return st.session_state.get(f"v21_desk_{name}")


def _desk_ready(result: Optional[Dict[str, Any]]) -> bool:
    """Render gate for a desk payload: show the refusal reason, else say ready."""
    if not result:
        return False
    if not result.get("available"):
        _unavailable(result.get("reason"))
        return False
    return True


def _bias_accent(bias: str) -> str:
    return C["green"] if bias == "LONG" else C["red"] if bias == "SHORT" else C["mute"]


def _tab_desks(key: str) -> None:
    """
    Six borrowed desks, rebuilt locally (item 19 lives in Agentic AI).

    Each desk names its reference product, says when a number is a proxy and
    reports the REA artefact it used — ``None`` means clean-room rebuild.
    """
    st.caption("Clean-room rebuilds of six reference workflows — no assets, code or "
               "branding copied, and no clone claimed.")
    _quote_strip(key)

    st.markdown("#### Order ticket · item 7")
    c1, c2, c3 = st.columns(3)
    side = c1.selectbox("Side", ["BUY", "SELL"], key="v21_ticket_side")
    capital = c2.number_input("Capital ₹", 50_000.0, 100_000_000.0, 500_000.0,
                              50_000.0, key="v21_ticket_cap")
    risk = c3.number_input("Risk per trade %", 0.1, 5.0, 1.0, 0.1, key="v21_ticket_risk")
    ticket = _desk("order ticket", desks.order_ticket, index=key, side=side,
                   capital=capital, risk_pct=risk)
    if _desk_ready(ticket):
        m = ticket["metrics"]
        _card(ticket["label"], ticket["detail"] + _kv([
            ("lots", m["lots"]), ("notional", f"₹{m['notional']:,.0f}"),
            ("at risk", f"₹{m['risk_total']:,.0f}")]),
            accent=C["gold"], badge=ticket["bias"])
        st.caption(ticket["margin_note"])
    acct = _desk("account", desks.account_snapshot, capital=capital)
    if _desk_ready(acct):
        _card(acct["label"], acct["detail"], accent=C["blue"], badge="paper")
        if acct["orders"]:
            st.dataframe(pd.DataFrame(acct["orders"]), hide_index=True, width="stretch")

    st.divider()
    st.markdown("#### Smart vs retail flow · item 8")
    flow = _desk("flow", desks.flow_tape, index=key)
    if _desk_ready(flow):
        _card(flow["label"], flow["detail"], accent=_bias_accent(flow["bias"]),
              badge=flow["bias"])
        st.dataframe(pd.DataFrame(flow["rows"]), hide_index=True, width="stretch")
        st.caption(f"{flow['source']} · {flow['delay']} · {flow['note']}")

    st.divider()
    st.markdown("#### Risk profile & cone · item 9")
    c1, c2 = st.columns([2, 1])
    strat = c1.selectbox("Structure", ["long_straddle", "short_straddle",
                                       "long_strangle", "short_strangle"], key="v21_rp_strat")
    width = c2.number_input("Strangle width (strikes)", 1, 6, 2, key="v21_rp_w")
    rp = _desk("risk profile", desks.risk_profile, index=key, strategy=strat,
               width=int(width))
    if _desk_ready(rp):
        _card(rp["label"], rp["detail"], accent=C["blue"], badge=rp["bias"])
        st.line_chart(pd.DataFrame(rp["rows"]).set_index("spot"))
        st.caption(f"{rp['source']} · {rp['note']}")
    cone = _desk("cone", desks.probability_cone, index=key)
    if _desk_ready(cone):
        _card(cone["label"], cone["detail"], accent=C["gold"], badge=cone["bias"])
        st.dataframe(pd.DataFrame(cone["bands"]), hide_index=True, width="stretch")

    st.divider()
    st.markdown("#### Indicators · item 10")
    ch = _desk("chart", desks.chart_desk, index=key)
    if _desk_ready(ch):
        _card(ch["label"], ch["detail"], accent=_bias_accent(ch["bias"]), badge=ch["bias"])
        frame = pd.DataFrame(ch["rows"]).set_index("date")
        st.line_chart(frame[["close", "ema20", "ema50"]])
        st.caption(f"support {ch['metrics']['support']:,.0f} · resistance "
                   f"{ch['metrics']['resistance']:,.0f} · RSI14 {ch['metrics']['rsi14']}")

    st.divider()
    st.markdown("#### Replay · item 23")
    rep = _desk("replay", desks.replay_frames, index=key)
    if _desk_ready(rep):
        _card(rep["label"], rep["detail"], accent=C["gold"], badge=rep["bias"])
        frames = rep["frames"]
        if len(frames) > 1:
            i = st.slider("Replay bar", 0, len(frames) - 1, len(frames) - 1,
                          key="v21_replay_bar")
            st.dataframe(pd.DataFrame([frames[i]]), hide_index=True, width="stretch")
            st.line_chart(pd.DataFrame(frames).set_index("bar")[["equity_pct", "drawdown_pct"]])
        st.caption(rep["note"])

    st.divider()
    st.markdown("#### Order-book imbalance · item 24")
    liq = _desk("liquidity", desks.liquidity_scan, index=key)
    if _desk_ready(liq):
        _card(liq["label"], liq["detail"], accent=_bias_accent(liq["bias"]),
              badge=liq["bias"])
        st.dataframe(pd.DataFrame(liq["rows"]), hide_index=True, width="stretch")
        st.caption(f"{liq['source']} · {liq['delay']} · {liq['note']}")


# ── entry point ────────────────────────────────────────────────────────────

def render_v21_panel() -> None:
    """Top-level renderer called from app.py."""
    _css()
    key = _index_selector()
    tabs = st.tabs(["Suggestions", "Trading", "Desks", "Research", "Agentic AI",
                    "Integrations", "Forecast"])
    with tabs[0]:
        _tab_suggestions(key)
    with tabs[1]:
        _tab_trading(key)
    with tabs[2]:
        _tab_desks(key)
    with tabs[3]:
        _tab_research(key)
    with tabs[4]:
        _tab_agentic(key)
    with tabs[5]:
        _tab_integrations(key)
    with tabs[6]:
        _tab_forecast(key)
