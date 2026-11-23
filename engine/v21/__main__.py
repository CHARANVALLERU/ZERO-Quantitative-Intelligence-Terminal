"""
v2.1 command line.

    python -m engine.v21 status              # wiring + integration registry
    python -m engine.v21 gex [--index NIFTY]
    python -m engine.v21 suggest [--index NIFTY]
    python -m engine.v21 eco   [--index NIFTY] [--genomes 6]
    python -m engine.v21 report [--index NIFTY]
    python -m engine.v21 doctor              # which optional modules are live
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.v21 import agents, ecosystem, forecast, gex, indices, integrations, reports, signals, trading


def cmd_status(_args) -> int:
    s = integrations.summary()
    print(json.dumps({"integrations": s,
                      "models": [{"name": m["name"], "available": m["available"]}
                                 for m in forecast.status()],
                      "indices": indices.index_options()}, indent=2))
    return 0


def cmd_doctor(_args) -> int:
    """Report which optional pieces are wired and reachable — no writes."""
    checks = {}

    def _try(name, fn):
        try:
            checks[name] = fn()
        except Exception as exc:  # noqa: BLE001
            checks[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    _try("options_snapshot", lambda: {"ok": True, "latest": bool(
        __import__("data.options_snapshot", fromlist=["latest"]).latest("NIFTY"))})
    _try("pit_store", lambda: {"ok": __import__("data.pit_store", fromlist=["x"]) is not None})
    _try("shadow", lambda: {"ok": bool(__import__("engine.v2.shadow", fromlist=["x"]))})
    _try("alerts", lambda: {"ok": True, "channels": __import__(
        "engine.v2.alerts", fromlist=["channels"]).channels()})
    _try("broker_history", lambda: __import__(
        "engine.broker.historical", fromlist=["status"]).status())
    _try("order_state", lambda: {"ok": bool(__import__(
        "engine.broker.order_state", fromlist=["x"]))})
    _try("kronos", lambda: next(m for m in forecast.status() if m["key"] == "kronos")["detail"])
    _try("reports_dir", lambda: {"ok": os.path.isdir(reports.REPORTS_DIR),
                                 "existing": len(reports.list_reports(100))})
    print(json.dumps(checks, indent=2, default=str))
    return 0


def cmd_gex(args) -> int:
    print(json.dumps(gex.live(args.index), indent=2, default=str))
    return 0


def cmd_suggest(args) -> int:
    key = indices.canonical(args.index)
    out = {"index": key, "quote": indices.quote(key), "engines": []}
    try:
        from engine.aite.exam import load_market_frame
        df = load_market_frame(indices.aite_symbol(key), bars=300)
        out["engines"].append(signals.chart_context(df))
    except Exception as exc:  # noqa: BLE001
        out["engines"].append({"engine": "pattern", "available": False, "reason": str(exc)})
    out["engines"].append(gex.live(key))
    print(json.dumps(out, indent=2, default=str))
    return 0


def cmd_eco(args) -> int:
    print(json.dumps(ecosystem.run_cycle(args.index, n_genomes=args.genomes),
                     indent=2, default=str))
    return 0


def cmd_report(args) -> int:
    r = reports.build_research_report(args.index, include_agents=args.agents)
    print(json.dumps({k: v for k, v in r.items() if k != "markdown"}, indent=2))
    return 0


def cmd_agents(args) -> int:
    print(json.dumps(agents.scan(args.tickers), indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="engine.v21")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("status", cmd_status), ("doctor", cmd_doctor),
                     ("gex", cmd_gex), ("suggest", cmd_suggest),
                     ("eco", cmd_eco), ("report", cmd_report), ("agents", cmd_agents)):
        p = sub.add_parser(name)
        p.set_defaults(func=fn)
        if name != "status" and name != "doctor":
            p.add_argument("--index", default="NIFTY")
        if name == "eco":
            p.add_argument("--genomes", type=int, default=6)
        if name == "report":
            p.add_argument("--agents", action="store_true")
        if name == "agents":
            p.add_argument("--tickers", nargs="+", default=["AAPL", "MSFT"])
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
