"""
The v2.1 **Integrations** registry — one record per requested item.

Why a registry instead of hard-coded buttons: the panel, `v2.1.MD` and the audit
all read the same list, so a feature cannot be "done" in the UI while the tracking
document still says otherwise. Status vocabulary is deliberately blunt:

``live``      implemented and callable in this repo (engine function or panel tab)
``partial``   working path plus a documented gap (e.g. needs a model download)
``link``      opens the third-party site; nothing is scraped or cloned
``external``  needs software outside this repo (REA, a cloned app) to exist
``planned``   specified, not built yet

Nothing here claims a capability the code does not have.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

STATUSES = ("live", "partial", "link", "external", "planned")


@dataclass
class Integration:
    id: str
    label: str
    item: int
    panel: str
    status: str
    kind: str                      # engine | site | software | feed | model
    detail: str
    action: str = ""               # url, "run:<dotted.func>" or "tab:<name>"
    tags: List[str] = field(default_factory=list)
    gap: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _i(*args, **kwargs) -> Integration:
    return Integration(*args, **kwargs)


REGISTRY: List[Integration] = [
    # ── panels / UI ─────────────────────────────────────────────────────────
    _i("panel.suggestions", "Suggestions", 1, "Suggestions", "live", "engine",
       "Ranked trade ideas with engine label, confidence, invalidation and chart.",
       "tab:Suggestions", ["ui", "core"]),
    _i("panel.trading", "Trading", 1, "Trading", "live", "engine",
       "Index selector, live F&O chain, GEX ladder, commodities and heatmap.",
       "tab:Trading", ["ui", "core"]),
    _i("panel.research", "Research", 1, "Research", "live", "engine",
       "SSRN digests, course-derived quant maths, OpenBB/Alpaca links, report export.",
       "tab:Research", ["ui", "core"]),
    _i("panel.agentic", "Agentic AI", 1, "Agentic AI", "live", "engine",
       "24/7 ecosystem flow, debate/insider/stress detectors, run history.",
       "tab:Agentic AI", ["ui", "agents"]),
    _i("panel.integrations", "Integrations", 1, "Integrations", "live", "engine",
       "This registry: every item with its label, status and button.",
       "tab:Integrations", ["ui", "core"]),
    _i("panel.ux", "UI/UX smoothing pass", 1, "Integrations", "partial", "engine",
       "v2.1 tabs share one card system, one palette and lazy network calls; the "
       "legacy v1/v2 panels still use their own styles.",
       "tab:Integrations", ["ui"], "legacy panels not yet migrated to the shared kit"),

    # ── research / data ─────────────────────────────────────────────────────
    _i("research.ssrn", "SSRN", 2, "Research", "live", "site",
       "Topic-scoped SSRN searches for strategies, equities, bonds, commodities and indicators.",
       "https://www.ssrn.com/index.cfm/en/fin/", ["research"]),
    _i("research.courses", "MIT / Stanford course quant", 12, "Research", "partial", "engine",
       "Bond pricing & duration, mean-variance frontier, PCA common factors, "
       "volatility, Black-Scholes and a solved convex problem.",
       "tab:Research", ["research", "quant"],
       "course video/notes are not downloaded; the maths is implemented locally"),
    _i("research.openbb", "OpenBB + Alpaca", 20, "Research", "partial", "software",
       "50+ source research fan-out and a REPORTS/ markdown export.",
       "https://github.com/OpenBB-finance/OpenBB", ["research", "data"],
       "openbb package is not installed; panel falls back to yfinance + links"),
    _i("research.alphaledger", "AlphaLedger", 21, "Suggestions", "link", "site",
       "Top-trader track records to mirror as suggestions.", "https://alphaledger.ai/",
       ["research", "suggestions"], "no public API; link-out only"),

    # ── feeds ───────────────────────────────────────────────────────────────
    _i("feed.fno", "Futures & options live", 3, "Trading", "partial", "feed",
       "Index spot/futures via NSE + yfinance; option chain with OI, IV and max pain.",
       "run:engine.v21.trading.fno_board", ["trading", "data"],
       "NSE chain is 1–3 s delayed and rate-limited; no tick-level feed without a broker"),
    _i("feed.commodities", "Commodities live", 15, "Trading", "live", "feed",
       "Gold, silver, crude, copper, nat-gas futures plus the mine→ship→port→mill chain.",
       "run:engine.v21.trading.commodities", ["trading", "data"]),
    _i("feed.gex", "Gamma exposure (GEX)", 4, "Trading", "live", "engine",
       "Per-strike dealer hedge, call/put walls, gamma flip and regime playbook.",
       "run:engine.v21.gex.live", ["trading", "options"]),
    _i("feed.headlines", "Market-moving headlines", 5, "Research", "partial", "feed",
       "FinancialJuice-style squawk sources with a global priority filter.",
       "https://www.financialjuice.com/home", ["news"],
       "aggregation uses the existing news pipeline; no FinancialJuice scrape"),
    _i("engine.patterns", "Chart pattern taxonomy", 17, "Suggestions", "live", "engine",
       "Sweep (reject) · breakout (accept) · retest (defend) · reversion (return to value).",
       "run:engine.v21.signals.chart_context", ["charts", "suggestions"]),
    _i("feed.openinsider", "OpenInsider bulk buys", 18, "Trading", "partial", "site",
       "Insider cluster-buy screen with a toast-level alert hook.",
       "http://openinsider.com/latest-cluster-buys", ["insider", "alerts"],
       "screen is read from the public page; no guaranteed uptime"),
    _i("feed.macro", "Gold / oil / yields priority", 22, "Research", "live", "engine",
       "Swing-frame gold filter, oil always-on, yields→inflation linkage.",
       "run:engine.v21.signals.macro_priority", ["news", "macro"]),

    # ── models / agents ─────────────────────────────────────────────────────
    _i("model.hedge", "Hedge predictor (OLS)", 6, "Suggestions", "live", "engine",
       "Beta, R², hedge notional and dollar-swing reduction from the attached image.",
       "run:engine.v21.signals.hedge_regression", ["model", "suggestions"]),
    _i("model.statarb", "Statistical arbitrage", 6, "Suggestions", "live", "engine",
       "Pairs z-score engine from the attached image with live entry/exit signal.",
       "run:engine.v21.signals.stat_arb", ["model", "suggestions"]),
    _i("model.forecast", "Forecast model adapters", 16, "Forecast", "partial", "model",
       "Registry for KRONOS, FINCAST, CHRONOS-2, TimesFM, TiREX-2 and Moirai: each runs "
       "only on click and charts only its own output.",
       "tab:Forecast", ["model", "forecast"],
       "the six repos are not vendored; adapters report 'not installed' honestly"),
    _i("agent.ecosystem", "Trading ecosystem flow", 13, "Agentic AI", "live", "engine",
       "Strategy creator → backtester → regime detector → arbitrage → risk → trade builder.",
       "run:engine.v21.ecosystem.run_cycle", ["agents", "core"]),
    _i("agent.debate", "Bull vs bear SEC debate", 14, "Agentic AI", "partial", "engine",
       "Two advocates argue from SEC EDGAR filings; a judge scores the winner.",
       "run:engine.v21.agents.debate", ["agents", "sec"],
       "LLM calls need OPENAI_API_KEY; without it the panel runs a rules-based fallback"),
    _i("agent.insider", "Insider cluster-buy radar", 14, "Agentic AI", "partial", "engine",
       "Flags several executives buying the same name inside a window (EDGAR + pandas).",
       "run:engine.v21.agents.insider_clusters", ["agents", "sec"],
       "PostgreSQL sink optional; JSONL ledger used when no database is configured"),
    _i("agent.stress", "Earnings-call stress detector", 14, "Agentic AI", "partial", "engine",
       "Scores nervous price action into earnings and checks whether it predicted the move.",
       "run:engine.v21.agents.earnings_stress", ["agents"],
       "librosa/FinBERT optional; falls back to realised-vol stress when absent"),

    # ── reference desks (clean-room rebuilds, items 7–10, 19, 23, 24) ───────
    _i("app.robinhood", "Order ticket + account", 7, "Desks", "partial", "engine",
       "Risk-first sizing, levels, notional and the local order lifecycle ledger.",
       "run:engine.v21.desks.order_ticket", ["broker", "desk"],
       "clean-room rebuild; no broker session, so SPAN margin and fills are not shown"),
    _i("app.bigshort", "Smart vs retail flow", 8, "Desks", "partial", "engine",
       "OI build read as supply/demand, split by contract size.",
       "run:engine.v21.desks.flow_tape", ["flow", "desk"],
       "size is a proxy for institution vs retail; NSE tags no participant per strike"),
    _i("app.thinkorswim", "Risk profile + probability cone", 9, "Desks", "partial", "engine",
       "Expiry payoff around the money, breakevens, premium at risk and a realised-vol cone.",
       "run:engine.v21.desks.risk_profile", ["options", "desk"],
       "expiry payoff only; the chain carries no time-to-expiry for greeks"),
    _i("app.tradingview", "Indicator desk", 10, "Desks", "partial", "engine",
       "EMA stack, RSI, ATR, Bollinger bands, support/resistance on the index's own bars.",
       "run:engine.v21.desks.chart_desk", ["charts", "desk"],
       "no TradingView embed or licence; indicators are computed locally"),
    _i("app.finviz", "Finviz heatmaps", 11, "Trading", "partial", "engine",
       "Sector/index heatmap computed locally from live quotes; Finviz as reference.",
       "run:engine.v21.trading.heatmap", ["heatmap"], ""),
    _i("app.dasreplay", "Bar-by-bar replay", 23, "Desks", "partial", "engine",
       "Replays a breakout rule frame by frame with position, levels and running equity.",
       "run:engine.v21.desks.replay_frames", ["replay", "desk"],
       "replays our own rule; not a DAS simulation and no hot keys"),
    _i("app.vibetrading", "Specialist agent desk", 19, "Agentic AI", "partial", "engine",
       "Momentum, reversion, flow and risk specialists rolled into one memo by consensus.",
       "run:engine.v21.desks.agent_desk", ["agents", "desk"],
       "deterministic specialists; LLM prose needs OPENAI_API_KEY"),
    _i("app.qliquidity", "Order-book imbalance", 24, "Desks", "partial", "engine",
       "Displayed bid/ask quantity imbalance per strike, aggregated across the chain.",
       "run:engine.v21.desks.liquidity_scan", ["microstructure", "desk"],
       "pending quantity is a depth proxy; no full level-2 on the free feed"),
]


def all_integrations() -> List[Dict[str, Any]]:
    return [i.to_dict() for i in REGISTRY]


def by_panel(panel: str) -> List[Dict[str, Any]]:
    return [i.to_dict() for i in REGISTRY if i.panel.lower() == panel.lower()]


def get(integration_id: str) -> Optional[Dict[str, Any]]:
    for i in REGISTRY:
        if i.id == integration_id:
            return i.to_dict()
    return None


def summary() -> Dict[str, Any]:
    counts = {s: 0 for s in STATUSES}
    for i in REGISTRY:
        counts[i.status] = counts.get(i.status, 0) + 1
    return {
        "total": len(REGISTRY),
        "counts": counts,
        "items": counts.get("live", 0) + counts.get("partial", 0),
        "external_blocked": counts.get("external", 0),
    }


def resolve_action(action: str) -> Dict[str, Any]:
    """
    Turn a registry ``action`` into something the panel can execute.

    ``http...`` → open, ``tab:NAME`` → switch tab, ``rea://x`` → external blocker,
    ``run:dotted.path`` → importable callable (checked, not executed).
    """
    a = (action or "").strip()
    if a.startswith("http"):
        return {"kind": "url", "url": a, "ok": True}
    if a.startswith("tab:"):
        return {"kind": "tab", "tab": a[4:], "ok": True}
    if a.startswith("rea://"):
        return {"kind": "external", "target": a[6:], "ok": False,
                "reason": "requires the REA desktop app to extract the target"}
    if a.startswith("run:"):
        path = a[4:]
        try:
            import importlib
            mod, _, attr = path.rpartition(".")
            getattr(importlib.import_module(mod), attr)
            return {"kind": "callable", "path": path, "ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"kind": "callable", "path": path, "ok": False, "reason": str(exc)}
    return {"kind": "none", "ok": False, "reason": "no action"}
