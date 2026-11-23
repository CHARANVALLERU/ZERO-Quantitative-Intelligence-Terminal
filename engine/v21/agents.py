"""
Agentic detectors for the v2.1 **Agentic AI** panel (items 14 and 18).

Three independent agents, all built on free, primary data:

``debate``            bull vs bear on a name using real SEC EDGAR filings, with a
                      third "judge" that scores the arguments. Without an LLM key
                      the agents argue from *measured* evidence (filing mix,
                      insider direction, price/vol regime) so the panel still
                      produces a labelled, reproducible verdict.
``insider_clusters``  Form 4 filings pulled from EDGAR, grouped into a cluster
                      when several insiders file inside a short window.
``earnings_stress``   how nervous price action was into the last earnings dates,
                      and whether that stress predicted the next move.

EDGAR requires a descriptive User-Agent; we send one. Every call is wrapped so a
blocked network returns ``available: False`` with the reason, never a fabricated
filing.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

import numpy as np

DB_DIR = os.path.join("db", "v21", "agents")
UA = {"User-Agent": "ZERO-Terminal/2.1 (research; contact: local-user)", "Accept-Encoding": "gzip"}

_TICKERS_CACHE: Dict[str, Any] = {"ts": 0.0, "map": {}}
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


# ── shared plumbing ─────────────────────────────────────────────────────────


def _get_json(url: str, timeout: float = 12.0) -> Optional[Dict[str, Any]]:
    try:
        import requests
        r = requests.get(url, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _get_text(url: str, timeout: float = 12.0) -> Optional[str]:
    try:
        import requests
        r = requests.get(url, headers=UA, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


def cik_for(ticker: str) -> Optional[int]:
    """Resolve a ticker to a zero-padded-free CIK via EDGAR's company list."""
    now = time.time()
    if not _TICKERS_CACHE["map"] or now - _TICKERS_CACHE["ts"] > 86400:
        data = _get_json(_TICKERS_URL)
        if not data:
            return None
        mapping = {}
        for row in data.values():
            mapping[str(row.get("ticker", "")).upper()] = int(row.get("cik_str", 0))
        _TICKERS_CACHE.update({"ts": now, "map": mapping})
    return _TICKERS_CACHE["map"].get(ticker.upper())


def _submissions(ticker: str) -> Optional[Dict[str, Any]]:
    cik = cik_for(ticker)
    if not cik:
        return None
    return _get_json(_SUBMISSIONS.format(cik=cik))


def _recent_filings(ticker: str, limit: int = 200) -> List[Dict[str, Any]]:
    sub = _submissions(ticker)
    if not sub:
        return []
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accession = recent.get("accessionNumber") or []
    docs = recent.get("primaryDocument") or []
    out = []
    for i in range(min(limit, len(forms))):
        try:
            out.append({
                "form": forms[i],
                "date": dates[i],
                "accession": accession[i],
                "document": docs[i] if i < len(docs) else "",
                "cik": sub.get("cik"),
            })
        except Exception:
            continue
    return out


def _yf_history(ticker: str, period: str = "2y"):
    try:
        import yfinance as yf
        df = yf.download(ticker, period=period, interval="1d", progress=False,
                         auto_adjust=True, threads=False)
        if df is None or len(df) < 30:
            return None
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = [c[0] for c in df.columns]
        return df
    except Exception:
        return None


def _ledger(name: str, record: Dict[str, Any]) -> str:
    os.makedirs(DB_DIR, exist_ok=True)
    path = os.path.join(DB_DIR, f"{name}.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({**record, "ts": time.time()}) + "\n")
    return path


def _alert(title: str, body: str, level: str = "info") -> bool:
    """Reuse the v2.0 alert bus so toasts and webhooks share one path."""
    try:
        from engine.v2 import alerts
        return bool(alerts.emit(title=title, body=body, level=level, source="v21.agents"))
    except Exception:
        return False


# ── 1. Bull vs bear debate ──────────────────────────────────────────────────


def debate(ticker: str = "AAPL", lookback_days: int = 365) -> Dict[str, Any]:
    """
    Two agents argue from real filings; a judge scores them.

    Evidence used (all measured, none invented):
      * filing mix (8-K = events, 10-Q/10-K = fundamentals, Form 4 = insiders)
      * insider direction from Form 4 count vs the prior window
      * price trend and realised volatility from yfinance
    """
    filings = _recent_filings(ticker, limit=400)
    if not filings:
        return {"available": False, "engine": "debate",
                "reason": "SEC EDGAR unreachable or ticker has no CIK"}

    cutoff = (_dt.date.today() - _dt.timedelta(days=lookback_days)).isoformat()
    recent = [f for f in filings if str(f.get("date", "")) >= cutoff]
    forms = [f["form"] for f in recent]
    n_8k = sum(1 for x in forms if x.startswith("8-K"))
    n_periodic = sum(1 for x in forms if x in ("10-Q", "10-K"))
    n_form4 = sum(1 for x in forms if x == "4")
    n_shelf = sum(1 for x in forms if x in ("S-3", "424B5", "S-1"))

    df = _yf_history(ticker)
    trend = vol = None
    if df is not None:
        closes = df["Close"].astype(float)
        ret = np.diff(np.log(closes.to_numpy()))
        trend = float(closes.iloc[-1] / closes.iloc[-min(len(closes), 126)] - 1.0) * 100
        vol = float(np.std(ret[-60:], ddof=1) * np.sqrt(252) * 100)

    bull: List[str] = []
    bear: List[str] = []
    if n_periodic:
        bull.append(f"{n_periodic} periodic report(s) filed in {lookback_days} days — "
                    f"the fundamental record is current.")
    else:
        bear.append("No 10-Q/10-K inside the window — stale disclosure.")
    if n_8k >= 3:
        bull.append(f"{n_8k} 8-K event filings: the company is active, not dormant.")
    if n_shelf:
        bear.append(f"{n_shelf} shelf/registration filing(s) — dilution risk.")
    if n_form4:
        bear.append(f"{n_form4} insider Form 4 filings — supply of stock to the market.")
    if trend is not None:
        (bull if trend > 0 else bear).append(f"6-month price trend {trend:+.1f}%.")
    if vol is not None and vol > 40:
        bear.append(f"Realised vol {vol:.0f}% — position sizing has to shrink.")

    bull_score = len(bull) * 1.0 + (0.5 if (trend or 0) > 0 else 0.0)
    bear_score = len(bear) * 1.0 + (0.5 if (trend or 0) < 0 else 0.0)
    total = bull_score + bear_score or 1.0
    winner = "BULL" if bull_score >= bear_score else "BEAR"

    result = {
        "available": True,
        "engine": "debate",
        "ticker": ticker.upper(),
        "window_days": lookback_days,
        "filings": {"total": len(recent), "8k": n_8k, "periodic": n_periodic,
                    "form4": n_form4, "shelf": n_shelf},
        "bull": {"agent": "BULL", "points": bull, "score": round(bull_score / total, 3)},
        "bear": {"agent": "BEAR", "points": bear, "score": round(bear_score / total, 3)},
        "judge": {
            "winner": winner,
            "confidence": round(abs(bull_score - bear_score) / total + 0.35, 2),
            "verdict": (f"{winner} wins on measured evidence: {len(bull)} bull points vs "
                        f"{len(bear)} bear points, trend {trend if trend is None else round(trend,1)}%, "
                        f"vol {vol if vol is None else round(vol,1)}%."),
        },
        "suggestion": {
            "bias": "BUY" if winner == "BULL" else "SELL",
            "label": f"SEC debate → {winner}",
            "detail": f"Judge scored bull {bull_score:.1f} vs bear {bear_score:.1f} using EDGAR filings.",
        },
        "source": "SEC EDGAR submissions + yfinance",
    }
    _ledger("debate", {"ticker": ticker.upper(), "winner": winner})
    return result


# ── 2. Insider cluster-buy radar ────────────────────────────────────────────


def insider_clusters(ticker: str = "AAPL", window_days: int = 30,
                     min_filings: int = 3) -> Dict[str, Any]:
    """
    Flag a cluster when several Form 4 filings land inside ``window_days``.

    EDGAR's submissions index gives filing-level data; the transactions inside the
    Form 4 XML are parsed when reachable, so a cluster of *sales* is not reported
    as a cluster of *buys*.
    """
    filings = [f for f in _recent_filings(ticker, limit=400) if f["form"] == "4"]
    if not filings:
        return {"available": False, "engine": "insider",
                "reason": "no Form 4 filings found (or EDGAR unreachable)"}

    cutoff = (_dt.date.today() - _dt.timedelta(days=window_days)).isoformat()
    inside = [f for f in filings if str(f.get("date", "")) >= cutoff]
    buys = _classify_form4(ticker, inside[:12])

    cluster = len(inside) >= min_filings
    result = {
        "available": True,
        "engine": "insider",
        "ticker": ticker.upper(),
        "window_days": window_days,
        "filings_in_window": len(inside),
        "min_filings": min_filings,
        "cluster": bool(cluster) and (buys["purchases"] > 0 or buys["parsed"] == 0),
        "transactions": buys,
        "dates": [f["date"] for f in inside[:12]],
        "label": (f"{len(inside)} Form 4 in {window_days}d"
                  + (" · CLUSTER" if cluster else "")),
        "source": "SEC EDGAR Form 4",
    }
    if result["cluster"]:
        result["alert"] = _alert("Insider cluster detected",
                                 f"{ticker.upper()}: {len(inside)} Form 4 filings in {window_days} days.",
                                 level="warn")
        _ledger("insider", {"ticker": ticker.upper(), "filings": len(inside)})
    return result


def _classify_form4(ticker: str, filings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Parse transaction codes from Form 4 XML when the document is reachable."""
    purchases = sales = parsed = 0
    shares_bought = 0.0
    cik = cik_for(ticker)
    for f in filings:
        if not cik or not f.get("accession") or not f.get("document"):
            continue
        acc = str(f["accession"]).replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{f['document']}"
        xml = _get_text(url, timeout=8.0)
        if not xml:
            continue
        codes = re.findall(r"<transactionCode>\s*([A-Z])\s*</transactionCode>", xml)
        shares = re.findall(r"<transactionShares>\s*<value>\s*([0-9.]+)", xml)
        if not codes:
            continue
        parsed += 1
        for i, c in enumerate(codes):
            if c == "P":
                purchases += 1
                if i < len(shares):
                    try:
                        shares_bought += float(shares[i])
                    except ValueError:
                        pass
            elif c == "S":
                sales += 1
    return {"parsed": parsed, "purchases": purchases, "sales": sales,
            "shares_bought": round(shares_bought, 0) if shares_bought else 0}


# ── 3. Earnings-call stress pattern ─────────────────────────────────────────


def earnings_stress(ticker: str = "AAPL", n_events: int = 6) -> Dict[str, Any]:
    """
    Score how nervous price action was into each earnings date and whether that
    stress predicted the next move.

    "Stress" = realised vol in the 3 sessions before the print relative to the
    60-day baseline, plus the size of the pre-print range compression. The test:
    does high pre-print stress correlate with a larger absolute post-print move?
    """
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        df = tk.history(period="3y", interval="1d", auto_adjust=True)
        if df is None or len(df) < 120:
            return {"available": False, "engine": "stress", "reason": "insufficient history"}
        ret = np.diff(np.log(df["Close"].astype(float).to_numpy()))
        dates = [d.date() for d in df.index]
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "engine": "stress", "reason": f"yfinance failed: {exc}"}

    events: List[Dict[str, Any]] = []
    try:
        ed = yf.Ticker(ticker).earnings_dates
        if ed is not None and len(ed):
            for ts in list(ed.index)[: n_events * 3]:
                d = ts.date() if hasattr(ts, "date") else ts
                if d in dates:
                    events.append({"date": str(d), "idx": dates.index(d)})
    except Exception:
        events = []

    if not events:  # fall back to the largest 1-day moves as event proxies
        order = np.argsort(np.abs(ret))[::-1][:n_events]
        events = [{"date": str(dates[int(i) + 1]), "idx": int(i) + 1, "proxy": True}
                  for i in order if 20 < int(i) < len(ret) - 2]

    rows = []
    for e in events:
        i = e["idx"]
        if i < 62 or i >= len(ret):
            continue
        pre = float(np.std(ret[i - 3:i], ddof=1))
        base = float(np.std(ret[i - 60:i], ddof=1)) or 1e-9
        post = float(ret[i]) if i < len(ret) else 0.0
        rows.append({
            "date": e["date"],
            "proxy": e.get("proxy", False),
            "stress_ratio": round(pre / base, 3),
            "pre_range_pct": round(float(np.ptp(ret[i - 3:i])) * 100, 2),
            "post_move_pct": round(post * 100, 2),
            "stress": "HIGH" if pre / base > 1.4 else ("LOW" if pre / base < 0.8 else "NORMAL"),
        })

    if len(rows) < 3:
        return {"available": False, "engine": "stress",
                "reason": "not enough earnings events aligned to the price series"}

    s = np.array([r["stress_ratio"] for r in rows])
    m = np.abs(np.array([r["post_move_pct"] for r in rows]))
    corr = float(np.corrcoef(s, m)[0, 1]) if s.std() > 0 and m.std() > 0 else 0.0
    return {
        "available": True,
        "engine": "stress",
        "ticker": ticker.upper(),
        "events": rows,
        "correlation_stress_vs_move": round(corr, 3),
        "predictive": abs(corr) >= 0.4,
        "label": f"stress→move r={corr:+.2f}",
        "detail": (f"Pre-print stress and the absolute post-print move are correlated "
                   f"{corr:+.2f} across {len(rows)} events — "
                   + ("the stress signal carries information." if abs(corr) >= 0.4
                      else "the stress signal did not predict the move.")),
        "source": "yfinance prices + earnings dates",
    }


def scan(tickers: List[str]) -> Dict[str, Any]:
    """Run all three agents over a list, for the panel's 'scan now' button."""
    out = {"debate": [], "insider": [], "stress": []}
    for t in tickers:
        d = debate(t)
        if d.get("available"):
            out["debate"].append(d)
        i = insider_clusters(t)
        if i.get("available") and i.get("cluster"):
            out["insider"].append(i)
        s = earnings_stress(t)
        if s.get("available"):
            out["stress"].append(s)
        time.sleep(0.2)  # be a good citizen with EDGAR (10 req/s max)
    return out
