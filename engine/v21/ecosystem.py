"""
The 24/7 trading ecosystem (item 13).

Six stages, each one a plain function so it can be run by the panel button, by
the scheduler, or by a test:

    strategy creator → backtester → regime detector
                     → market arbitrage → risk management → trade builder

The output is (a) a strategy spec written to ``strategies/v21/`` only when the
backtest earns it, and (b) a ranked suggestion for the Suggestions panel. Every
stage records what it did, including the stages that reject, so the panel can show
the reasoning instead of a black box.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np

from engine.v21 import indices as idx
from engine.v21.quant import ema

V21_STRATEGY_DIR = os.path.join("strategies", "v21")
LEDGER = os.path.join("db", "v21", "ecosystem.json")
DEFAULT_LEDGER_LIMIT = 50      # newest records kept in ecosystem.json
DEFAULT_SPEC_LIMIT = 40        # newest specs kept per index in strategies/v21/


def _limit(env_name: str, default: int) -> int:
    """Env-tunable cap, read per call so a long run can be re-tuned."""
    try:
        return max(1, int(os.getenv(env_name, str(default))))
    except (TypeError, ValueError):
        return default


def ledger_limit() -> int:
    return _limit("ZERO_V21_LEDGER_LIMIT", DEFAULT_LEDGER_LIMIT)


def spec_limit() -> int:
    return _limit("ZERO_V21_SPEC_LIMIT", DEFAULT_SPEC_LIMIT)


def _atomic_json(path: str, data: Any) -> None:
    """Write via temp + replace so a crash mid-write cannot truncate the file."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    os.replace(tmp, path)


def _log(stages: List[Dict[str, Any]], name: str, status: str, detail: str,
         data: Optional[Dict[str, Any]] = None, ms: Optional[float] = None) -> None:
    stages.append({"stage": name, "status": status, "detail": detail,
                   "data": data or {}, "ms": round(ms, 1) if ms else None})


def _frame(index: str, bars: int = 420):
    from engine.aite.exam import load_market_frame
    return load_market_frame(idx.aite_symbol(index), bars=bars)


def regime(index: str = "NIFTY") -> Dict[str, Any]:
    """Trend / volatility regime from the index's own daily bars."""
    df = _frame(index, bars=400)
    if df is None or len(df) < 210:
        return {"available": False, "reason": "insufficient bars for regime"}
    close = df["close"].astype(float).to_numpy()
    ema50 = ema(close, 50)
    ema200 = ema(close, 200)
    ret = np.diff(np.log(close))
    vol20 = float(np.std(ret[-20:], ddof=1) * np.sqrt(252) * 100)
    vol_pct = float((np.std(ret[-60:], ddof=1) * np.sqrt(252) * 100))
    trend = "up" if ema50[-1] > ema200[-1] else "down"
    vol_regime = "high" if vol20 > vol_pct * 1.25 else ("low" if vol20 < vol_pct * 0.75 else "normal")
    bias = {"up": "LONG", "down": "SHORT"}[trend]
    if vol_regime == "high":
        bias = "SMALL"      # high vol: same direction, smaller size
    return {
        "available": True,
        "engine": "regime",
        "trend": trend,
        "vol_regime": vol_regime,
        "vol20_annual_pct": round(vol20, 2),
        "vol60_annual_pct": round(vol_pct, 2),
        "label": f"{trend}-trend · {vol_regime} vol",
        "bias": bias,
        "detail": (f"EMA50 {'above' if trend == 'up' else 'below'} EMA200 with "
                   f"{vol20:.1f}% 20-day vol vs {vol_pct:.1f}% baseline."),
    }


def arbitrage(index: str = "NIFTY") -> Dict[str, Any]:
    """Cross-index stat-arb: the selected index against its closest peer."""
    peer = {"NIFTY": "BANKNIFTY", "BANKNIFTY": "NIFTY", "SENSEX": "NIFTY"}[idx.canonical(index)]
    try:
        a = _frame(index, bars=400)["close"].astype(float).to_numpy()
        b = _frame(peer, bars=400)["close"].astype(float).to_numpy()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"peer data unavailable: {exc}"}
    n = min(len(a), len(b))
    if n < 300:
        return {"available": False, "reason": "need 300 aligned bars for pairs"}
    from engine.v21.signals import stat_arb
    out = stat_arb(a[-n:], b[-n:], name_a=idx.canonical(index), name_b=peer)
    out["peer"] = peer
    return out


def creator(index: str, n_genomes: int = 6, seed: Optional[int] = None) -> List[Any]:
    """Stage 1 — breed candidate genomes for the selected index."""
    from engine.aite import breeding
    breeder = breeding.Breeder(seed=seed or int(time.time()) % 100000)
    return [breeder.random_genome(symbol=idx.aite_symbol(index)) for _ in range(n_genomes)]


def backtester(genomes: List[Any], df) -> List[Dict[str, Any]]:
    """Stage 2 — run the real exam on real bars; keep the verdicts."""
    from engine.aite.exam import run_exam
    out = []
    for g in genomes:
        ex = run_exam(g, df)
        out.append({"bot_id": g.bot_id, "style": g.style, "passed": bool(ex.passed),
                    "reason": ex.reason, "oos_sharpe": float(ex.oos_sharpe),
                    "is_sharpe": float(ex.is_sharpe), "oos_return": float(ex.oos_return),
                    "max_dd": float(ex.max_dd), "trades_oos": int(ex.n_trades_oos),
                    "genome": g.to_dict()})
    out.sort(key=lambda r: (r["passed"], r["oos_sharpe"]), reverse=True)
    return out


def risk_gate(candidates: List[Dict[str, Any]], max_dd: float = 0.20) -> Dict[str, Any]:
    """Stage 5 — the book-level veto, applied before anything is suggested."""
    if not candidates:
        return {"available": False, "reason": "no candidates survived the backtest"}
    top = candidates[0]
    ok = top["passed"] and top["max_dd"] <= max_dd and top["trades_oos"] >= 3
    return {
        "available": True,
        "engine": "risk",
        "passed": bool(ok),
        "max_dd": round(top["max_dd"], 4),
        "limit": max_dd,
        "detail": ("risk gate cleared" if ok else
                   f"vetoed: passed={top['passed']} dd={top['max_dd']*100:.1f}% "
                   f"oos_trades={top['trades_oos']}"),
    }


def trade_builder(index: str, candidate: Optional[Dict[str, Any]],
                  regime_out: Dict[str, Any], gex_out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Stage 6 — turn the surviving genome into one concrete trade idea."""
    q = idx.quote(index)
    if not q.get("available"):
        return {"available": False, "reason": "no live quote for the selected index"}
    spot = float(q["last"])
    atr = _atr(index)
    if candidate is None:
        bias = regime_out.get("bias", "WAIT")
        return {
            "available": True,
            "engine": "builder",
            "label": f"{idx.label(index)} · {regime_out.get('label','no regime')}",
            "bias": bias,
            "confidence": 0.35,
            "detail": "No genome earned promotion; showing the regime-only stance.",
            "levels": _levels(spot, atr, "LONG" if bias in ("LONG", "SMALL") else "SHORT"),
            "source": "regime only",
        }
    side = "SHORT" if candidate["style"] == "mean_reversion" and regime_out.get("trend") == "down" else "LONG"
    conf = 0.5 + max(0.0, min(0.3, candidate["oos_sharpe"] / 10.0))
    gex_label = ""
    if gex_out and gex_out.get("available"):
        gex_label = f" · GEX {gex_out['regime']}"
        if gex_out["regime"] == "positive":
            conf = max(0.3, conf - 0.08)  # positive gamma: fade, don't chase
    return {
        "available": True,
        "engine": "builder",
        "label": f"{candidate['bot_id']} · {candidate['style']} · {regime_out.get('label','')}{gex_label}",
        "bias": side,
        "confidence": round(conf, 2),
        "detail": (f"OOS Sharpe {candidate['oos_sharpe']:.2f} over {candidate['trades_oos']} trades, "
                   f"max DD {candidate['max_dd']*100:.1f}%, regime {regime_out.get('label','n/a')}."),
        "levels": _levels(spot, atr, side),
        "invalidation": f"regime flips to {('down' if side=='LONG' else 'up')} or ATR stop hit",
        "source": "ecosystem",
    }


def _atr(index: str, window: int = 14) -> float:
    try:
        df = _frame(index, bars=120)
        h, l, c = (df[x].astype(float).to_numpy() for x in ("high", "low", "close"))
        tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
        return float(np.mean(tr[-window:]))
    except Exception:
        return 0.0


def _levels(spot: float, atr: float, side: str) -> Dict[str, float]:
    stop = 1.5 * atr if atr else spot * 0.01
    take = 2.5 * atr if atr else spot * 0.02
    if side == "LONG":
        return {"entry": round(spot, 2), "stop": round(spot - stop, 2),
                "target": round(spot + take, 2), "atr": round(atr, 2)}
    return {"entry": round(spot, 2), "stop": round(spot + stop, 2),
            "target": round(spot - take, 2), "atr": round(atr, 2)}


def run_cycle(index: str = "NIFTY", n_genomes: int = 6,
              persist: bool = True) -> Dict[str, Any]:
    """Run the full ecosystem once and return the stage-by-stage record."""
    t_all = time.time()
    key = idx.canonical(index)
    stages: List[Dict[str, Any]] = []

    t = time.time()
    df = _frame(key, bars=420)
    if df is None or len(df) < 260:
        return {"available": False, "reason": "not enough real bars to run the ecosystem"}
    from engine.data_provenance import frame_provenance
    prov = frame_provenance(df)
    _log(stages, "data", "ok", f"{len(df)} bars · {prov.source} · {prov.vendor}", ms=(time.time()-t)*1000)

    t = time.time()
    genomes = creator(key, n_genomes=n_genomes)
    _log(stages, "strategy_creator", "ok", f"{len(genomes)} genomes bred", ms=(time.time()-t)*1000)

    t = time.time()
    results = backtester(genomes, df)
    n_pass = sum(1 for r in results if r["passed"])
    _log(stages, "backtester", "ok" if n_pass else "empty",
         f"{n_pass}/{len(results)} passed promotion", {"passed": n_pass}, ms=(time.time()-t)*1000)

    t = time.time()
    reg = regime(key)
    _log(stages, "regime_detector", "ok" if reg.get("available") else "unavailable",
         reg.get("label", reg.get("reason", "")), ms=(time.time()-t)*1000)

    t = time.time()
    arb = arbitrage(key)
    _log(stages, "market_arbitrage", "ok" if arb.get("available") else "unavailable",
         arb.get("label", arb.get("reason", "")), ms=(time.time()-t)*1000)

    t = time.time()
    survivors = [r for r in results if r["passed"]]
    risk = risk_gate(survivors or results[:1])
    _log(stages, "risk_management", "ok" if risk.get("passed") else "veto",
         risk.get("detail", ""), ms=(time.time()-t)*1000)

    gex_out = None
    try:
        from engine.v21 import gex as gex_mod
        gex_out = gex_mod.live(key)
    except Exception:
        gex_out = {"available": False, "reason": "gex unavailable"}

    t = time.time()
    best = survivors[0] if (survivors and risk.get("passed")) else None
    suggestion = trade_builder(key, best, reg, gex_out)
    _log(stages, "trade_builder", "ok" if suggestion.get("available") else "unavailable",
         suggestion.get("label", suggestion.get("reason", "")), ms=(time.time()-t)*1000)

    spec_path = None
    if best is not None and persist:
        spec_path = _persist_spec(key, best, reg, suggestion)

    out = {
        "available": True,
        "index": key,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(time.time() - t_all, 1),
        "provenance": prov.as_dict(),
        "stages": stages,
        "survivors": [{k: v for k, v in s.items() if k != "genome"} for s in survivors],
        "regime": reg,
        "arbitrage": arb,
        "risk": risk,
        "gex": gex_out if isinstance(gex_out, dict) else None,
        "suggestion": suggestion,
        "strategy_spec": spec_path,
    }
    if persist:
        _write_ledger(out)
        _publish(out)
    return out


def _publish(out: Dict[str, Any]) -> None:
    """Fan the cycle out to the v2.0 alert bus and shadow ledger."""
    sg = out.get("suggestion") or {}
    try:
        if sg.get("available") and float(sg.get("confidence") or 0) >= 0.65:
            from engine.v2 import alerts
            alerts.notify(
                level="info",
                title=f"v2.1 suggestion · {out['index']}",
                body=f"{sg.get('label')} — {sg.get('detail')}",
            )
    except Exception:
        pass
    try:
        from engine.v2 import shadow
        shadow.record_day(out["index"])
    except Exception:
        pass


def _persist_spec(index: str, best: Dict[str, Any], reg: Dict[str, Any],
                  suggestion: Dict[str, Any]) -> str:
    os.makedirs(V21_STRATEGY_DIR, exist_ok=True)
    path = os.path.join(V21_STRATEGY_DIR, f"{index.lower()}_{best['bot_id']}.json")
    spec = {
        "spec_version": "2.1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "index": index,
        "bot_id": best["bot_id"],
        "style": best["style"],
        "genome": best["genome"],
        "backtest": {k: v for k, v in best.items() if k != "genome"},
        "regime": reg,
        "suggestion": suggestion,
        "provenance": "real_vendor_bars",
    }
    _atomic_json(path, spec)
    _prune_specs(index)
    return path


def _prune_specs(index: str, keep: Optional[int] = None) -> int:
    """Keep the newest ``keep`` specs for one index; older ones are deleted.

    Genome ids are random, so an unattended loop would otherwise add one small
    file per promoted cycle forever.
    """
    limit = keep if keep is not None else spec_limit()
    prefix = f"{index.lower()}_bot_"
    try:
        names = [n for n in os.listdir(V21_STRATEGY_DIR)
                 if n.startswith(prefix) and n.endswith(".json")]
    except OSError:
        return 0
    if len(names) <= limit:
        return 0
    def _mtime(name: str) -> float:
        try:
            return os.path.getmtime(os.path.join(V21_STRATEGY_DIR, name))
        except OSError:
            return 0.0
    names.sort(key=_mtime, reverse=True)
    removed = 0
    for name in names[limit:]:
        try:
            os.remove(os.path.join(V21_STRATEGY_DIR, name))
            removed += 1
        except OSError:
            pass
    return removed


def _write_ledger(record: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    history: List[Any] = []
    if os.path.exists(LEDGER):
        try:
            with open(LEDGER, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except Exception:
            history = []
    history.append({k: record[k] for k in ("index", "generated_at", "seconds", "suggestion", "regime")})
    _atomic_json(LEDGER, history[-ledger_limit():])


def history(limit: int = 20) -> List[Dict[str, Any]]:
    if not os.path.exists(LEDGER):
        return []
    try:
        with open(LEDGER, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data[-limit:] if isinstance(data, list) else []
    except Exception:
        return []
