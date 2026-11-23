"""
Research report builder (item 20).

Collects whatever the v2.1 engines can honestly produce for one index, writes a
Markdown report into ``REPORTS/`` and returns its path. Sections that could not be
built say so in the report — a report is a record of what was measured, not a
sales brochure.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from engine.v21 import indices as idx

REPORTS_DIR = "REPORTS"

# Curated SSRN lanes (item 2). Deep links are stable search URLs, so the panel
# never depends on a scraped result set.
SSRN_TOPICS = [
    {"key": "strategies", "label": "Trading strategies",
     "url": "https://papers.ssrn.com/sol3/results.cfm?txtKey_Words=trading+strategy+alpha"},
    {"key": "equities", "label": "Equities",
     "url": "https://papers.ssrn.com/sol3/results.cfm?txtKey_Words=equity+return+predictability"},
    {"key": "bonds", "label": "Bonds & rates",
     "url": "https://papers.ssrn.com/sol3/results.cfm?txtKey_Words=bond+yield+term+structure"},
    {"key": "commodities", "label": "Commodities",
     "url": "https://papers.ssrn.com/sol3/results.cfm?txtKey_Words=commodity+futures+carry"},
    {"key": "indicators", "label": "Trading indicators",
     "url": "https://papers.ssrn.com/sol3/results.cfm?txtKey_Words=technical+indicators+backtest"},
    {"key": "vol", "label": "Volatility & options",
     "url": "https://papers.ssrn.com/sol3/results.cfm?txtKey_Words=implied+volatility+gamma+exposure"},
]

COURSES = [
    {"label": "MIT 15.401 Finance Theory", "url": "https://ocw.mit.edu/courses/15-401-finance-theory-i-fall-2008/",
     "covers": "present value, fixed income, mean-variance, CAPM"},
    {"label": "MIT 18.S096 Mathematics with Applications in Finance",
     "url": "https://ocw.mit.edu/courses/18-s096-topics-in-mathematics-with-applications-in-finance-fall-2013/",
     "covers": "regression, PCA, volatility modelling, Black-Scholes"},
    {"label": "Stanford EE364a Convex Optimization",
     "url": "https://web.stanford.edu/class/ee364a/",
     "covers": "convex problems, portfolio optimisation, duality"},
]


def _ensure_dir() -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return REPORTS_DIR


def _md_table(rows: List[Dict[str, Any]], cols: List[str]) -> str:
    if not rows:
        return "_(no rows)_\n"
    head = "| " + " | ".join(cols) + " |\n|" + "|".join(["---"] * len(cols)) + "|\n"
    body = "".join(
        "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n" for r in rows
    )
    return head + body


def build_research_report(index: str = "NIFTY", include_agents: bool = False) -> Dict[str, Any]:
    """Collect live sections, render Markdown, write ``REPORTS/<index>_<ts>.md``."""
    key = idx.canonical(index)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    sections: Dict[str, Any] = {}

    q = idx.quote(key)
    sections["quote"] = q

    try:
        from engine.v21 import trading
        sections["fno"] = trading.fno_board(key)
        sections["heatmap"] = trading.heatmap(key)
        sections["commodities"] = trading.commodities()
    except Exception as exc:  # noqa: BLE001
        sections["trading_error"] = str(exc)

    try:
        from engine.v21 import signals
        sections["macro"] = signals.macro_priority(
            gold_change_pct=_pct(sections, "commodities", "gold"),
            oil_change_pct=_pct(sections, "commodities", "crude"),
            yield_10y=None, yield_10y_prev=None,
        )
    except Exception:
        sections["macro"] = []

    if include_agents:
        try:
            from engine.v21 import agents
            sections["debate"] = agents.debate("RELIANCE.NS")
        except Exception:
            pass

    md = _render(key, stamp, sections)
    path = os.path.join(_ensure_dir(), f"{key.lower()}_{stamp}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return {"available": True, "path": path, "bytes": len(md.encode("utf-8")),
            "sections": list(sections), "markdown": md}


def _pct(sections: Dict[str, Any], group: str, key: str) -> Optional[float]:
    rows = (sections.get(group) or {}).get("rows") or []
    for r in rows:
        if r.get("key") == key:
            return r.get("change_pct")
    return None


def _render(key: str, stamp: str, s: Dict[str, Any]) -> str:
    q = s.get("quote") or {}
    lines: List[str] = []
    lines.append(f"# ZERO Research Report — {idx.label(key)}")
    lines.append("")
    lines.append(f"- generated: `{time.strftime('%Y-%m-%d %H:%M:%S')}`")
    lines.append(f"- engine: v2.1 · index `{key}`")
    lines.append("")
    lines.append("## 1. Price")
    if q.get("available"):
        lines.append(f"- last **{q['last']:,.2f}** ({q.get('change_pct'):+.2f}%) "
                     f"source `{q.get('source')}`")
    else:
        lines.append(f"- unavailable: {q.get('reason')}")
    lines.append("")

    lines.append("## 2. Futures & options")
    fno = s.get("fno") or {}
    chain = fno.get("chain") if isinstance(fno.get("chain"), dict) else {}
    if chain and not chain.get("error"):
        lines.append(f"- PCR **{chain.get('pcr')}**, max pain **{chain.get('max_pain')}**, "
                     f"expiry `{chain.get('nearest_expiry')}`")
        lines.append(f"- call OI wall {chain.get('max_ce_oi_strike')}, "
                     f"put OI wall {chain.get('max_pe_oi_strike')}")
    else:
        lines.append(f"- chain unavailable: {(chain or {}).get('error', 'no data')}")
    gex = (fno.get("gex") or {})
    if gex.get("available"):
        t = gex["totals"]
        lines.append(f"- GEX regime **{gex['regime']}** · flip `{gex.get('gamma_flip')}` · "
                     f"net {t['net_gex_bn']} bn pts · call wall {t['call_wall']} / put wall {t['put_wall']}")
        lines.append(f"- playbook: {gex['playbook']}")
    lines.append("")

    lines.append("## 3. Sector heatmap")
    hm = s.get("heatmap") or {}
    if hm.get("available"):
        lines.append(f"- breadth: {hm['breadth']['advancing']}/{hm['breadth']['total']} advancing")
        lines.append("")
        lines.append(_md_table(hm["sectors"], ["sector", "change_pct", "weight"]))
    else:
        lines.append(f"- unavailable: {hm.get('error') or 'no data'}")
    lines.append("")

    lines.append("## 4. Commodities")
    cm = s.get("commodities") or {}
    if cm.get("available"):
        lines.append(_md_table(cm["rows"], ["name", "last", "change_pct", "unit"]))
        lines.append("")
        lines.append("Physical chain: " + " → ".join(x["stage"] for x in cm.get("supply_chain", [])))
    else:
        lines.append(f"- unavailable: {cm.get('error') or 'no data'}")
    lines.append("")

    lines.append("## 5. Macro priority")
    macro = s.get("macro") or []
    if macro:
        for m in macro:
            lines.append(f"- **{m['asset']}** [{m['priority']}] — {m['message']}")
    else:
        lines.append("- no macro inputs available")
    lines.append("")

    d = s.get("debate")
    if d and d.get("available"):
        lines.append("## 6. SEC debate")
        lines.append(f"- judge: **{d['judge']['winner']}** — {d['judge']['verdict']}")
        for p in d["bull"]["points"]:
            lines.append(f"  - bull: {p}")
        for p in d["bear"]["points"]:
            lines.append(f"  - bear: {p}")
        lines.append("")

    lines.append("## Sources")
    lines.append("- NSE option chain (delayed), yfinance futures/equities, SEC EDGAR submissions.")
    lines.append("-")
    lines.append("_Sections marked unavailable were not measured; no value is estimated._")
    return "\n".join(lines) + "\n"


def list_reports(limit: int = 30) -> List[Dict[str, Any]]:
    if not os.path.isdir(REPORTS_DIR):
        return []
    out = []
    for name in sorted(os.listdir(REPORTS_DIR), reverse=True):
        if not name.endswith(".md"):
            continue
        path = os.path.join(REPORTS_DIR, name)
        try:
            out.append({"name": name, "path": path,
                        "bytes": os.path.getsize(path),
                        "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                               time.localtime(os.path.getmtime(path)))})
        except OSError:
            continue
        if len(out) >= limit:
            break
    return out
