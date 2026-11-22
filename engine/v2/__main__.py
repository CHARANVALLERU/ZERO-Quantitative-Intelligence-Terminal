"""
ZERO Engine v2.0 — command line
===============================

Usage (from the project root):

    python -m engine.v2 status
    python -m engine.v2 macro            # refresh + analyse the macro graph
    python -m engine.v2 lab            # generate/backtest/validate strategies
    python -m engine.v2 research         # run the institutional desks
    python -m engine.v2 audit --repair   # integrity audit (and safe repairs)
    python -m engine.v2 rea-probe        # discover REA MCP tools
    python -m engine.v2 brief            # print today's session brief
    python -m engine.v2 provenance       # is the AITE book built on real bars?
    python -m engine.v2 risk             # portfolio-level risk of the validated book
    python -m engine.v2 overfit          # PBO / deflated Sharpe for the lab
    python -m engine.v2 shadow --record --score
    python -m engine.v2 costs            # real Indian cost stack vs the flat proxy
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _print(payload: Any) -> None:
    text = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + "\n")


def cmd_status() -> int:
    from engine.v2 import macro_factors, rea_bridge, session_brief, state_audit, strategy_lab
    report = {
        "schema": "2.0",
        "audit": {k: v for k, v in state_audit.audit().items() if k != "checks"},
        "registry": {
            "total": strategy_lab.load_registry().get("total", 0),
            "passed": strategy_lab.load_registry().get("passed", 0),
            "updated_at": strategy_lab.load_registry().get("updated_at"),
        },
        "rea": rea_bridge.status(),
        "macro_snapshot": bool(macro_factors.load_last_snapshot()),
        "brief": session_brief.summary_line(),
    }
    _print(report)
    return 0


def cmd_macro(allow_network: bool) -> int:
    from engine.v2 import macro_factors
    prev = macro_factors.load_last_snapshot()
    snap = macro_factors.fetch_snapshot(allow_network=allow_network)
    analysis = macro_factors.analyze(snap, prev)
    if snap.coverage() > 0:
        macro_factors.save_snapshot(snap)
    _print(analysis)
    return 0


def cmd_lab(refresh: bool) -> int:
    from engine.v2 import strategy_lab
    result = strategy_lab.run_lab(refresh_data=refresh)
    _print({
        "created": result["created"],
        "passed": result["passed"],
        "validated": result["validated"],
        "skipped": result["skipped"],
        "top": result["top"],
    })
    return 0


def cmd_research() -> int:
    from engine.v2 import aggregator, macro_factors, market_structure, research_agents
    unified = aggregator.build_unified_view()
    snap = macro_factors.fetch_snapshot()
    analysis = macro_factors.analyze(snap, macro_factors.load_last_snapshot())
    studies = {}
    for key in ("NIFTY", "SENSEX"):
        import importlib
        historical = importlib.import_module("data.historical")
        df = historical.get_historical_data(key, period="1y")
        studies[key] = market_structure.study(df, key)
    desks = research_agents.run_desks(analysis, unified, studies)
    _print([{"desk": d["label"], "headline": d["headline"], "confidence": d["confidence"]}
            for d in desks])
    return 0


def cmd_audit(repair: bool) -> int:
    from engine.v2 import state_audit
    report = state_audit.audit()
    if repair:
        report["repair"] = state_audit.repair(apply=True)
        report = state_audit.audit() | {"repair": report["repair"]}
    _print(report)
    return 0 if report.get("high", 1) == 0 else 1


def cmd_rea_probe() -> int:
    from engine.v2 import rea_bridge
    _print(rea_bridge.probe_rea())
    return 0


def cmd_brief() -> int:
    from engine.v2 import session_brief
    _print(session_brief.toast_payload())
    return 0


def cmd_provenance() -> int:
    from engine import data_provenance
    report = data_provenance.audit_stored_state()
    _print(report)
    return 0 if report.get("verdict") == "clean" else 1


def cmd_risk() -> int:
    from engine.v2 import portfolio_risk
    payload = portfolio_risk.from_registry()
    _print(payload)
    return 0 if (payload.get("gate") or {}).get("passed") else 1


def cmd_overfit() -> int:
    from engine.v2 import overfitting, strategy_lab

    entries = (strategy_lab.load_registry().get("strategies") or [])[:20]
    trials = {}
    frames = {}
    for entry in entries:
        try:
            spec = strategy_lab.StrategySpec(
                family=str(entry.get("family") or "BREAKOUT"),
                index_key=str(entry.get("index") or "NIFTY"),
                params=dict(entry.get("params") or {}),
                name=str(entry.get("name") or ""),
            )
        except Exception:
            continue
        key = spec.index_key
        if key not in frames:
            frames[key] = strategy_lab._frame(key)
        if frames[key] is None or not len(frames[key]):
            continue
        result = strategy_lab.backtest(frames[key], spec)
        from engine.v2.portfolio_risk import daily_returns
        daily = daily_returns(result.get("trades") or [], strategy_lab.DEFAULT_CAPITAL)
        if len(daily) >= 20:
            trials[spec.name] = list(daily.values())
    if len(trials) < 2:
        _print({"status": "INSUFFICIENT_TRIALS", "trials": len(trials)})
        return 1
    _print(overfitting.summarise(trials))
    return 0


def cmd_shadow(record: bool, score: bool, index: str) -> int:
    from engine.v2 import shadow
    out = {}
    if record:
        out["record"] = shadow.record_day(index)
    if score:
        out["score"] = shadow.score_day(index)
    out["reconciliation"] = shadow.reconciliation()
    _print(out)
    return 0


def cmd_costs() -> int:
    from engine.v2 import costs
    _print(costs.cost_model_report())
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.v2", description="ZERO Engine v2.0")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="one-screen health/registry/MCP status")

    p_macro = sub.add_parser("macro", help="refresh and analyse the macro factor graph")
    p_macro.add_argument("--offline", action="store_true", help="use the input store only")

    p_lab = sub.add_parser("lab", help="generate, backtest and document strategies")
    p_lab.add_argument("--refresh", action="store_true", help="re-download the price cache")

    sub.add_parser("research", help="run the institutional research desks")
    sub.add_parser("rea-probe", help="discover the REA MCP tool catalogue")
    sub.add_parser("brief", help="print today's session brief")

    p_audit = sub.add_parser("audit", help="run the integrity audit")
    p_audit.add_argument("--repair", action="store_true", help="apply safe repairs")

    sub.add_parser("provenance", help="audit AITE survivor/portfolio data provenance")
    sub.add_parser("risk", help="portfolio-level risk of the validated book")
    sub.add_parser("overfit", help="PBO and deflated Sharpe for the lab run")
    sub.add_parser("costs", help="real Indian cost stack vs the flat bps proxy")

    p_shadow = sub.add_parser("shadow", help="paper-forward signals and reconciliation")
    p_shadow.add_argument("--record", action="store_true", help="append today's signals")
    p_shadow.add_argument("--score", action="store_true", help="score elapsed signals")
    p_shadow.add_argument("--index", default="NIFTY")

    args = parser.parse_args(argv)
    if args.command == "status":
        return cmd_status()
    if args.command == "macro":
        return cmd_macro(allow_network=not args.offline)
    if args.command == "lab":
        return cmd_lab(refresh=args.refresh)
    if args.command == "research":
        return cmd_research()
    if args.command == "audit":
        return cmd_audit(repair=args.repair)
    if args.command == "rea-probe":
        return cmd_rea_probe()
    if args.command == "brief":
        return cmd_brief()
    if args.command == "provenance":
        return cmd_provenance()
    if args.command == "risk":
        return cmd_risk()
    if args.command == "overfit":
        return cmd_overfit()
    if args.command == "costs":
        return cmd_costs()
    if args.command == "shadow":
        return cmd_shadow(args.record, args.score, args.index)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
