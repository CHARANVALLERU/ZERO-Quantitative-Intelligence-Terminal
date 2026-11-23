"""
Run the AITE exam path end-to-end on **real vendor data**.

This is the reproducibility script for the v2.1 "provenance path against real
vendor data" check: it loads NIFTY / SENSEX / BANKNIFTY through
``load_market_frame`` (which tags provenance), prints what the loader actually
returned, then breeds a handful of genomes and runs ``run_exam`` on each one so
the promotion gate is exercised on data the gate has to *accept*.

Usage
-----
    python scripts/run_real_exam.py                    # 3 indices, 4 genomes
    python scripts/run_real_exam.py --symbols NIFTY --genomes 2
    python scripts/run_real_exam.py --bars 500 --json db/v21/real_exam.json

Exit code is 0 when every index produced a promotable real frame; the exam
verdict (pass/fail) is reported but is not a failure of the data path — a bot
that fails its OOS gate is the gate doing its job.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.aite import breeding as breeding_mod  # noqa: E402
from engine.aite.exam import load_market_frame, run_exam  # noqa: E402
from engine.data_provenance import frame_provenance, is_promotable  # noqa: E402

DEFAULT_SYMBOLS = ["NIFTY", "SENSEX", "BANKNIFTY"]


def probe_symbol(symbol: str, bars: int, n_genomes: int, seed: int) -> Dict[str, Any]:
    t0 = time.time()
    df = load_market_frame(symbol, bars=bars)
    prov = frame_provenance(df)
    promotable, why = is_promotable(prov)

    out: Dict[str, Any] = {
        "symbol": symbol,
        "rows": int(len(df)) if df is not None else 0,
        "source": prov.source,
        "vendor": prov.vendor,
        "first_ts": prov.first_ts,
        "last_ts": prov.last_ts,
        "ohlc_violations": prov.ohlc_violations,
        "duplicate_bars": prov.duplicate_bars,
        "promotable": bool(promotable),
        "gate_reason": why,
        "load_seconds": round(time.time() - t0, 2),
        "exams": [],
    }
    if not promotable or df is None or len(df) == 0:
        return out

    breeder = breeding_mod.Breeder(seed=seed)
    for i in range(max(1, n_genomes)):
        genome = breeder.random_genome(symbol=symbol, generation=0)
        t1 = time.time()
        exam = run_exam(genome, df)
        out["exams"].append({
            "bot_id": genome.bot_id,
            "style": genome.style,
            "passed": bool(exam.passed),
            "reason": exam.reason,
            "data_source": exam.data_source,
            "is_sharpe": round(float(exam.is_sharpe), 3),
            "oos_sharpe": round(float(exam.oos_sharpe), 3),
            "oos_return_pct": round(float(exam.oos_return) * 100, 2),
            "max_dd_pct": round(float(exam.max_dd) * 100, 2),
            "trades_is": int(exam.n_trades_is),
            "trades_oos": int(exam.n_trades_oos),
            "seconds": round(time.time() - t1, 2),
        })
    out["passed"] = sum(1 for e in out["exams"] if e["passed"])
    out["examined"] = len(out["exams"])
    return out


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--bars", type=int, default=420)
    ap.add_argument("--genomes", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", default="")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    results = [probe_symbol(s, args.bars, args.genomes, args.seed) for s in args.symbols]

    for r in results:
        print(f"\n{r['symbol']:<10} rows={r['rows']:<5} source={r['source']:<11} "
              f"vendor={r['vendor'] or '-':<16} promotable={r['promotable']} ({r['gate_reason']})")
        print(f"           window {r['first_ts']} -> {r['last_ts']}  "
              f"ohlc_violations={r['ohlc_violations']} dupes={r['duplicate_bars']} "
              f"load={r['load_seconds']}s")
        if not r["exams"]:
            print("           no exam run (frame not promotable)")
            continue
        for e in r["exams"]:
            print(f"           {e['bot_id']:<14} {e['style']:<15} "
                  f"passed={str(e['passed']):<5} {e['reason']:<28} "
                  f"IS={e['is_sharpe']:>6.2f} OOS={e['oos_sharpe']:>6.2f} "
                  f"trades={e['trades_is']}/{e['trades_oos']}")
        print(f"           -> {r['passed']}/{r['examined']} passed promotion")

    if args.json:
        path = Path(args.json)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"generated_at": time.time(), "results": results}, indent=2),
                        encoding="utf-8")
        print(f"\nwrote {path}")

    return 0 if all(r["promotable"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
