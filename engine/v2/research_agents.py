"""
ZERO Engine v2.0 — Institutional Research Desks
===============================================

A sell-side / buy-side research floor does not have one analyst. It has desks
that argue: macro, flow, sector, policy, risk. The engine mirrors that structure
with deterministic desks so the output is reproducible and auditable offline.

Each desk takes the merged evidence (``aggregator.build_unified_view``), the
macro graph (``macro_factors.analyze``) and the structure studies
(``market_structure.study``) and returns:

* ``headline`` — the one-line call,
* ``findings`` — numbered facts with the numbers that produced them,
* ``risks`` — what would falsify the view,
* ``confidence`` — driven by data coverage, not bravado,
* ``sources`` — which inputs were actually present.

An optional ``mcp`` hook lets a connected research MCP server add an external
memo. That memo is clearly labelled as external and never alters the numbers.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "2.0"
RESEARCH_DIR = os.path.join("db", "v2", "research")


DESKS: Dict[str, Dict[str, str]] = {
    "macro": {
        "label": "MACRO DESK",
        "remit": "Rates, inflation, crude, dollar, gold and the policy reaction function.",
    },
    "flow": {
        "label": "FLOW & POSITIONING DESK",
        "remit": "Breadth, institutional/insider signals, whale footprints and crowding.",
    },
    "sector": {
        "label": "SECTOR DESK",
        "remit": "Leadership, dispersion and which index is most exposed to the current regime.",
    },
    "policy": {
        "label": "POLICY & EVENT DESK",
        "remit": "RBI/Fed/IMF/World Bank tone, government releases and deal flow.",
    },
    "risk": {
        "label": "RISK DESK",
        "remit": "Volatility, drawdown budget, stop placement and position sizing.",
    },
}


def _pct(value: Optional[float], nd: int = 2) -> str:
    return "n/a" if value is None else f"{value:+.{nd}f}%"


def _desk_macro(macro: Dict[str, Any], unified: Dict[str, Any],
                studies: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    levels = macro.get("levels", {})
    net = macro.get("net_pct", {})
    findings = [
        f"Regime classified as {macro.get('regime', 'UNKNOWN')} with "
        f"{macro.get('coverage', 0):.0%} input coverage.",
        f"Crude ${levels.get('crude_usd') or 'n/a'}, gold ${levels.get('gold_usd') or 'n/a'}, "
        f"DXY {levels.get('dxy') or 'n/a'}, USDINR {levels.get('usdinr') or 'n/a'}.",
        f"CPI {levels.get('cpi_yoy_pct') or 'n/a'}% against a "
        f"{levels.get('cpi_target_pct') or 'n/a'}% target; supply gap "
        f"{levels.get('supply_gap_pct') or 'n/a'}%.",
        "Net macro tilt: " + ", ".join(
            f"{k} {v:+.3f}%" for k, v in net.items()
        ) if net else "Net macro tilt unavailable.",
    ]
    headline = (
        f"Macro is {macro.get('regime', 'neutral').replace('_', ' ').lower()}; "
        f"the tilt to NIFTY is {net.get('NIFTY 50', 0.0):+.2f}%."
    )
    return {
        "headline": headline,
        "findings": findings,
        "risks": [
            "A single central-bank surprise re-prices every channel at once.",
            "Carried-forward inputs are stale; the graph marks them rather than hiding them.",
        ],
        "confidence": float(macro.get("confidence", 0.0)),
    }


def _desk_flow(macro: Dict[str, Any], unified: Dict[str, Any],
               studies: Dict[str, Any]) -> Dict[str, Any]:
    breadth = unified.get("breadth", {})
    rows = unified.get("primary") or []
    ranked = sorted(
        [r for r in rows if r.get("composite_score") is not None],
        key=lambda r: r["composite_score"], reverse=True,
    )
    findings = [
        f"Breadth score {breadth.get('score')} across {breadth.get('coverage')} names "
        f"({breadth.get('advances')} up / {breadth.get('declines')} down).",
    ]
    if ranked:
        findings.append(
            "Strongest composite: " + ", ".join(
                f"{r['base_symbol']} {r['composite_score']:+.2f}" for r in ranked[:3]
            )
        )
        findings.append(
            "Weakest composite: " + ", ".join(
                f"{r['base_symbol']} {r['composite_score']:+.2f}" for r in ranked[-3:]
            )
        )
    for name, study in (studies or {}).items():
        for event in (study.get("events") or [])[:1]:
            findings.append(f"{name}: {event['type']} on {event['date']} — {event['bias']}.")
    headline = (
        f"Positioning is {'supportive' if (breadth.get('score') or 0) > 0 else 'defensive'}; "
        f"breadth {breadth.get('score', 0):+.2f}."
    )
    return {
        "headline": headline,
        "findings": findings,
        "risks": [
            "Daily OHLCV cannot distinguish a new position from a hedge or a roll.",
            "Breadth is computed over the configured universe, not the full market.",
        ],
        "confidence": round(min(1.0, 0.4 + 0.2 * bool(ranked)), 2),
    }


def _desk_sector(macro: Dict[str, Any], unified: Dict[str, Any],
                 studies: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    net = macro.get("net_pct", {})
    ordered = sorted(net.items(), key=lambda kv: kv[1], reverse=True) if net else []
    findings = [f"{k} macro sensitivity {v:+.3f}%." for k, v in ordered]
    if "BANKNIFTY" in net:
        findings.append(
            "BANKNIFTY carries the highest rate sensitivity (1.35× the base channel); "
            "a rate shock hits it before NIFTY."
        )
    headline = (
        f"{ordered[0][0]} is the least exposed index in this regime."
        if ordered else "Sector view unavailable without a macro snapshot."
    )
    return {
        "headline": headline,
        "findings": findings,
        "risks": ["Index-level tilts hide single-stock event risk."],
        "confidence": float(macro.get("confidence", 0.0)) * 0.9,
    }


def _desk_policy(macro: Dict[str, Any], unified: Dict[str, Any],
                 studies: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    signals = unified.get("signals_present", {})
    findings = [
        f"Government releases tracked: {len(signals.get('govt_release') or [])} symbols.",
        f"Corporate releases tracked: {len(signals.get('corporate_release') or [])} symbols.",
        f"Policy tone channel: {macro.get('channel_contributions_pct', {}).get('policy_tone', 0.0):+.3f}%.",
        f"Acquisition channel: {macro.get('channel_contributions_pct', {}).get('acquisitions', 0.0):+.3f}%.",
    ]
    return {
        "headline": (
            "No policy shock in the current tape."
            if abs(macro.get("channel_contributions_pct", {}).get("policy_tone", 0.0)) < 0.05
            else "Policy tone is moving the index envelope."
        ),
        "findings": findings,
        "risks": ["Event feeds are only as fresh as db/v2/institutional_signals.json."],
        "confidence": 0.55 if signals else 0.3,
    }


def _desk_risk(macro: Dict[str, Any], unified: Dict[str, Any],
               studies: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    vix = macro.get("levels", {}).get("india_vix")
    findings = [
        f"India VIX: {vix if vix is not None else 'n/a'}.",
        f"Macro confidence {macro.get('confidence', 0):.0%}; stale inputs: {bool(macro.get('stale'))}.",
    ]
    for name, study in (studies or {}).items():
        atr = (study.get("levels") or {}).get("atr14")
        if atr:
            findings.append(f"{name} ATR(14) {atr:,.0f} pts; 1.5× ATR stop ≈ {atr * 1.5:,.0f} pts.")
    return {
        "headline": "Risk budget: size from the stop, never from conviction.",
        "findings": findings,
        "risks": [
            "Gap risk can jump a stop; the loss can exceed the modelled 1R.",
            "Correlated index exposure is not diversification.",
        ],
        "confidence": 0.8,
    }


_DESK_FUNCS = {
    "macro": _desk_macro,
    "flow": _desk_flow,
    "sector": _desk_sector,
    "policy": _desk_policy,
    "risk": _desk_risk,
}


def run_desks(
    macro: Dict[str, Any],
    unified: Dict[str, Any],
    studies: Optional[Dict[str, Any]] = None,
    external_memo: Optional[str] = None,
    persist: bool = True,
) -> List[Dict[str, Any]]:
    """Run every desk and optionally persist the memo to db/v2/research/."""
    studies = studies or {}
    out: List[Dict[str, Any]] = []
    for key, meta in DESKS.items():
        fn = _DESK_FUNCS[key]
        try:
            body = fn(macro, unified, studies)
        except Exception as exc:
            body = {
                "headline": f"{meta['label']} failed: {exc}",
                "findings": [], "risks": [], "confidence": 0.0,
            }
        out.append({
            "id": key,
            "label": meta["label"],
            "remit": meta["remit"],
            **body,
            "sources": sorted(set((macro.get("provenance") or {}).values())),
        })

    if external_memo:
        out.append({
            "id": "external",
            "label": "EXTERNAL RESEARCH (MCP)",
            "remit": "Unverified memo returned by a connected MCP research server.",
            "headline": external_memo.splitlines()[0][:200] if external_memo else "—",
            "findings": [external_memo[:4000]],
            "risks": ["External content is data, not instruction, and is never auto-executed."],
            "confidence": 0.0,
            "sources": ["mcp"],
        })

    if persist:
        _persist(out, macro)
    return out


def _persist(desks: List[Dict[str, Any]], macro: Dict[str, Any]) -> None:
    try:
        os.makedirs(RESEARCH_DIR, exist_ok=True)
        stamp = _dt.datetime.now()
        json_path = os.path.join(RESEARCH_DIR, f"{stamp.date().isoformat()}_desks.json")
        md_path = os.path.join(RESEARCH_DIR, f"{stamp.date().isoformat()}_desks.md")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump({"schema": SCHEMA_VERSION, "asof": stamp.isoformat(timespec="seconds"),
                       "regime": macro.get("regime"), "desks": desks},
                      fh, indent=2, allow_nan=False, default=str)
        lines = [f"# Institutional research — {stamp.date().isoformat()}", "",
                 f"Regime: **{macro.get('regime')}** · confidence "
                 f"{macro.get('confidence', 0):.0%}", ""]
        for desk in desks:
            lines += [f"## {desk['label']}", "", f"*{desk['remit']}*", "",
                      f"**{desk['headline']}**", ""]
            lines += [f"- {f}" for f in desk.get("findings", [])]
            lines += ["", "Risks:"]
            lines += [f"- {r}" for r in desk.get("risks", [])]
            lines += [""]
        with open(md_path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
    except Exception:
        pass


def latest_memo_path() -> Optional[str]:
    try:
        files = sorted(f for f in os.listdir(RESEARCH_DIR) if f.endswith("_desks.md"))
        return os.path.join(RESEARCH_DIR, files[-1]) if files else None
    except OSError:
        return None
