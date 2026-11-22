"""
ZERO Engine v2.0 — Macro Factor Graph
=====================================

Encodes the v2.0 macro thesis as an **explicit, auditable factor graph**
rather than a black box. Each relationship the desk asked for is a named
channel with a documented sign, elasticity and data requirement:

======  ==================================================================
Rule    Statement encoded here
======  ==================================================================
1       Crude up  -> USD down  -> equities down (India is a net importer).
2       Inflation up -> policy rate up -> discount rate up -> equities down,
        with a *second* nominal-earnings channel that can offset it. The net
        sign is regime dependent and reported as such.
3       Gold up -> USD down -> equities down (risk-off / real-yield proxy).
4       Crude vs gold positive; crude vs inflation / USD / equities negative
        *only under the Fed-money framing* (flagged ``fed_framing``).
5       Crude is also driven by the global supply balance (supply vs demand),
        not just Gulf/strait closures.
6       Gold vs inflation positive, vs equities and USD negative — but only
        while nothing is competitively opposing it (``gold_gate``).
7       CPI is a primary input to the inflation channel.
8       CAGR is an equity-growth channel (positive, slow-moving).
9       Acquisitions add a breadth / risk-appetite channel.
10      IMF / World Bank / RBI / Fed news is a policy-tone channel.
11      Aggregation happens upstream (``engine.v2.aggregator``); this module
        consumes the merged snapshot.
======  ==================================================================

The output is in **percent of index level**, so it can be applied to NIFTY,
BANKNIFTY and SENSEX without knowing their absolute prices. Total magnitude is
hard-capped (``MAX_TOTAL_PCT``) so the macro layer can *nudge* the GIFT/ADR
prediction core but never dominate it.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "2.0"

# ── Tunables (all elasticities are "percent of index move per 1 unit") ──────
# A 10% crude move -> -0.55% equities; a 25bp hike -> -0.09%; etc. These are
# deliberately conservative: the macro layer is a tilt, not a forecast.
ELASTICITY: Dict[str, float] = {
    "crude": -0.055,          # per 1% crude change
    "gold": -0.012,           # per 1% gold change
    "dxy": -0.030,            # per 1% dollar-index change
    "usdinr": -0.045,         # per 1% INR depreciation
    "inflation_gap": -0.060,  # per 1pp CPI above the 4% target
    "inflation_momentum": -0.040,  # per 1pp CPI change
    "policy_rate": -0.360,    # per 1pp policy-rate change (25bp ≈ -0.09%)
    "supply_deficit": -0.020,  # per 1% supply deficit
    "cagr": 0.020,            # per 1pp CAGR above the 10% baseline
    "acquisition": 0.150,     # per unit (-1..1) deal-breadth score
    "policy_tone": 0.250,     # per unit (-1..1) IMF/WB/RBI/Fed tone
    "insider": 0.120,         # per unit (-1..1) insider/flow tone
}

# Per-index sensitivity multipliers applied to the *rate* and *inflation*
# channels: banks are the most rate-sensitive, SENSEX carries heavier
# financials, NIFTY is the most export/IT weighted.
INDEX_TILT: Dict[str, Dict[str, float]] = {
    "NIFTY 50":  {"rates": 1.00, "crude": 1.00, "dxy": 1.00, "cagr": 1.00},
    "BANKNIFTY": {"rates": 1.35, "crude": 0.85, "dxy": 1.05, "cagr": 0.90},
    "SENSEX":    {"rates": 1.15, "crude": 0.95, "dxy": 1.00, "cagr": 1.00},
}

CPI_TARGET_PCT = 4.0        # RBI flexible inflation-targeting midpoint
CAGR_BASELINE_PCT = 10.0    # long-run Indian equity CAGR baseline
MAX_TOTAL_PCT = 0.60        # hard cap on the whole macro contribution
MIN_CONFIDENCE_TO_APPLY = 0.35

INPUT_STORE = os.path.join("db", "v2", "macro_inputs.json")
SNAPSHOT_STORE = os.path.join("db", "v2", "macro_snapshot.json")

# Manual-only fields: no live feed, so these come from macro_inputs.json and
# fall back to the previous snapshot (without flagging it stale).
_MANUAL_FIELDS = (
    "cpi_yoy_pct", "fed_funds_pct", "repo_rate_pct", "supply_gap_pct",
    "nifty_cagr_pct", "sensex_cagr_pct", "earnings_growth_pct",
)
_TONE_FIELDS = ("acquisition_score", "policy_tone", "insider_tone")

# Which INDEX_TILT multiplier applies to each channel. Unlisted channels use 1.0.
_TILT_GROUP = {
    "policy_rate": "rates",
    "inflation_valuation": "rates",
    "inflation_momentum": "rates",
    "inflation_nominal_earnings": "rates",
    "cagr": "cagr",
    "crude": "crude",
    "supply_balance": "crude",
    "dxy": "dxy",
    "usdinr": "dxy",
    "gold": "dxy",
}


# ═══════════════════════════════════════════════════════════════════════════
#  Snapshot
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class MacroSnapshot:
    """One point-in-time view of every macro input the graph consumes.

    Every numeric field is optional: ``None`` means "not observed", which
    lowers confidence instead of being silently treated as zero.
    """

    asof: str = ""
    # Market observables
    crude_usd: Optional[float] = None
    gold_usd: Optional[float] = None
    dxy: Optional[float] = None
    usdinr: Optional[float] = None
    india_vix: Optional[float] = None
    # Policy / macro
    cpi_yoy_pct: Optional[float] = None
    cpi_target_pct: float = CPI_TARGET_PCT
    fed_funds_pct: Optional[float] = None
    repo_rate_pct: Optional[float] = None
    # Real economy
    supply_gap_pct: Optional[float] = None   # + = deficit (demand > supply)
    nifty_cagr_pct: Optional[float] = None
    sensex_cagr_pct: Optional[float] = None
    earnings_growth_pct: Optional[float] = None
    # Event channels, normalized to [-1, 1]
    acquisition_score: float = 0.0
    policy_tone: float = 0.0
    insider_tone: float = 0.0
    # Bookkeeping
    provenance: Dict[str, str] = field(default_factory=dict)
    stale: bool = False
    notes: List[str] = field(default_factory=list)

    def coverage(self) -> float:
        """Fraction of demanded inputs that are actually present (0..1)."""
        checks = [
            self.crude_usd is not None,
            self.gold_usd is not None,
            self.dxy is not None,
            self.usdinr is not None,
            self.cpi_yoy_pct is not None,
            self.repo_rate_pct is not None or self.fed_funds_pct is not None,
            self.supply_gap_pct is not None,
            self.nifty_cagr_pct is not None or self.sensex_cagr_pct is not None,
        ]
        return sum(1 for c in checks if c) / float(len(checks))


def _num(value: Any) -> Optional[float]:
    """Coerce to float, rejecting NaN/inf/strings that are not numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def snapshot_from_dict(raw: Optional[Dict[str, Any]]) -> MacroSnapshot:
    """Build a snapshot from an arbitrary dict (file, API, UI form)."""
    raw = raw or {}
    known = {f for f in MacroSnapshot.__dataclass_fields__}  # type: ignore[attr-defined]
    kwargs: Dict[str, Any] = {}
    for key, value in raw.items():
        if key not in known:
            continue
        if key in ("asof", "notes"):
            kwargs[key] = value
        elif key == "provenance":
            kwargs[key] = dict(value or {})
        elif key == "stale":
            kwargs[key] = bool(value)
        else:
            number = _num(value)
            kwargs[key] = number if number is not None else value
    snap = MacroSnapshot(**kwargs)
    if not snap.asof:
        snap.asof = _dt.datetime.now().isoformat(timespec="seconds")
    return snap


# ═══════════════════════════════════════════════════════════════════════════
#  Load / persist
# ═══════════════════════════════════════════════════════════════════════════

def _read_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write_json(path: str, payload: Any) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, allow_nan=False, default=str)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError):
        return False


def load_macro_inputs() -> Dict[str, Any]:
    """Manually maintained inputs (CPI, rates, supply balance, deals).

    These are the numbers that cannot be scraped reliably every second. They
    live in ``db/v2/macro_inputs.json`` and are editable from the v2.0 panel.
    """
    data = _read_json(INPUT_STORE)
    return data if isinstance(data, dict) else {}


def load_last_snapshot() -> Optional[MacroSnapshot]:
    data = _read_json(SNAPSHOT_STORE)
    if not isinstance(data, dict):
        return None
    snap = snapshot_from_dict(data)
    return snap


def save_snapshot(snap: MacroSnapshot) -> bool:
    payload = asdict(snap)
    payload["schema"] = SCHEMA_VERSION
    return _write_json(SNAPSHOT_STORE, payload)


def _age_hours(iso_ts: str) -> Optional[float]:
    try:
        then = _dt.datetime.fromisoformat(str(iso_ts))
    except (TypeError, ValueError):
        return None
    delta = _dt.datetime.now() - then
    return max(0.0, delta.total_seconds() / 3600.0)


# ═══════════════════════════════════════════════════════════════════════════
#  Live fetch (never raises; returns provenance)
# ═══════════════════════════════════════════════════════════════════════════

_YF_SYMBOLS = {
    "crude_usd": "BZ=F",     # Brent front month
    "gold_usd": "GC=F",      # Gold front month
    "dxy": "DX-Y.NYB",       # Dollar index
    "usdinr": "USDINR=X",
    "india_vix": "^INDIAVIX",
}


def _download_last_close(symbols: Iterable[str]) -> Dict[str, float]:
    """Batch last close via yfinance. Returns {} on any failure."""
    try:
        import yfinance as yf
    except Exception:
        return {}
    out: Dict[str, float] = {}
    try:
        frame = yf.download(
            list(symbols), period="5d", interval="1d",
            progress=False, auto_adjust=False, group_by="ticker", threads=False,
        )
    except Exception:
        return {}
    if frame is None or getattr(frame, "empty", True):
        return {}
    try:
        for name, sym in _YF_SYMBOLS.items():
            if sym not in symbols:
                continue
            try:
                col = frame[sym]["Close"] if hasattr(frame.columns, "levels") else frame["Close"]
                series = col.dropna()
                if len(series):
                    out[name] = float(series.iloc[-1])
            except Exception:
                continue
    except Exception:
        return out
    return out


def _carry(
    snap: MacroSnapshot,
    prov: Dict[str, str],
    manual: Dict[str, Any],
    prev: Optional[MacroSnapshot],
    name: str,
    *,
    live: Optional[float] = None,
    clamp: bool = False,
    stale_on_carry: bool = False,
    provenance_on_carry: bool = True,
    prev_default: Optional[float] = None,
) -> None:
    """Resolve one field: live -> manual -> previous snapshot.

    The precedence and the bookkeeping it produces (provenance, the stale
    flag) are the contract; callers only choose which sources apply.
    """
    if live is not None:
        setattr(snap, name, live)
        prov[name] = "yfinance"
        return

    value = _num(manual.get(name))
    if value is not None:
        setattr(snap, name, _clamp(value) if clamp else value)
        prov[name] = str(manual.get(f"{name}_source") or "macro_inputs.json")
        return

    if prev is None:
        return
    carried = getattr(prev, name, None)
    if carried is None:
        if prev_default is not None:
            setattr(snap, name, prev_default)
        return
    setattr(snap, name, carried)
    if provenance_on_carry:
        prov[name] = "last_snapshot"
    if stale_on_carry:
        snap.stale = True


def fetch_snapshot(allow_network: bool = True) -> MacroSnapshot:
    """Assemble a snapshot from live quotes + the manual input store.

    Falls back to the last persisted snapshot for any field that could not be
    observed, and marks the result ``stale`` when the fallback is old.
    """
    manual = load_macro_inputs()
    prev = load_last_snapshot()
    prov: Dict[str, str] = {}
    notes: List[str] = []

    live: Dict[str, float] = {}
    if allow_network:
        live = _download_last_close(list(_YF_SYMBOLS.values()))
    prov["market"] = "yfinance:last_close" if live else "unavailable"

    snap = MacroSnapshot(
        asof=_dt.datetime.now().isoformat(timespec="seconds"),
        cpi_target_pct=_num(manual.get("cpi_target_pct")) or CPI_TARGET_PCT,
        provenance=prov,
        notes=notes,
    )

    for name in _YF_SYMBOLS:
        _carry(snap, prov, manual, prev, name, live=live.get(name), stale_on_carry=True)
    for name in _MANUAL_FIELDS:
        _carry(snap, prov, manual, prev, name)
    for name in _TONE_FIELDS:
        _carry(
            snap, prov, manual, prev, name,
            clamp=True, provenance_on_carry=False, prev_default=0.0,
        )

    age = _age_hours(str(manual.get("updated_at") or ""))
    if age is not None and age > 72:
        snap.stale = True
        notes.append(f"macro_inputs.json is {age:.0f}h old — CPI/rate/supply channels are stale.")
    if snap.stale:
        notes.append("Some fields were carried from the last snapshot; confidence reduced.")
    return snap


# ═══════════════════════════════════════════════════════════════════════════
#  The factor graph
# ═══════════════════════════════════════════════════════════════════════════

def _pct_change(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev) * 100.0


def _delta(cur: Optional[float], prev: Optional[float]) -> Optional[float]:
    if cur is None or prev is None:
        return None
    return cur - prev


def _rule(
    rule: int,
    statement: str,
    observed: str,
    status: str,
) -> Dict[str, Any]:
    return {
        "rule": rule,
        "statement": statement,
        "observed": observed,
        "status": status,
    }


def analyze(
    snap: MacroSnapshot,
    prev: Optional[MacroSnapshot] = None,
    fed_framing: bool = True,
) -> Dict[str, Any]:
    """Run the factor graph. Pure: no I/O, no network.

    Returns a dict with per-channel percent contributions, per-index net
    percent, a human narrative, and a rule-by-rule audit that says whether the
    live data currently confirms or contradicts each stated relationship.
    """
    prev = prev or MacroSnapshot()
    missing: List[str] = []

    crude_pct = _pct_change(snap.crude_usd, prev.crude_usd)
    gold_pct = _pct_change(snap.gold_usd, prev.gold_usd)
    dxy_pct = _pct_change(snap.dxy, prev.dxy)
    inr_pct = _pct_change(snap.usdinr, prev.usdinr)
    cpi_delta = _delta(snap.cpi_yoy_pct, prev.cpi_yoy_pct)
    rate_now = snap.repo_rate_pct if snap.repo_rate_pct is not None else snap.fed_funds_pct
    rate_prev = prev.repo_rate_pct if prev.repo_rate_pct is not None else prev.fed_funds_pct
    rate_delta = _delta(rate_now, rate_prev)

    for label, value in (
        ("crude", crude_pct), ("gold", gold_pct), ("dxy", dxy_pct), ("usdinr", inr_pct),
    ):
        if value is None:
            missing.append(label)

    # ── Channel contributions (% of index) ─────────────────────────────────
    contrib: Dict[str, float] = {}
    if crude_pct is not None:
        contrib["crude"] = ELASTICITY["crude"] * crude_pct
    if gold_pct is not None:
        contrib["gold"] = ELASTICITY["gold"] * gold_pct
    if dxy_pct is not None:
        contrib["dxy"] = ELASTICITY["dxy"] * dxy_pct
    if inr_pct is not None:
        contrib["usdinr"] = ELASTICITY["usdinr"] * inr_pct

    # Rule 2 + 7: inflation is two channels. Valuation (negative, via the
    # discount rate) and nominal earnings (positive). We report both so the UI
    # can show why a rate hike can coexist with a rising index.
    infl_gap = (snap.cpi_yoy_pct - snap.cpi_target_pct) if snap.cpi_yoy_pct is not None else None
    if infl_gap is not None:
        contrib["inflation_valuation"] = ELASTICITY["inflation_gap"] * infl_gap
        if cpi_delta is not None:
            contrib["inflation_momentum"] = ELASTICITY["inflation_momentum"] * cpi_delta
        nominal = _num(snap.earnings_growth_pct)
        if nominal is not None:
            # Only the part of nominal growth that CPI *caused* is attributed
            # here; real growth belongs to the CAGR channel.
            contrib["inflation_nominal_earnings"] = 0.010 * max(0.0, nominal)
    if rate_delta is not None:
        contrib["policy_rate"] = ELASTICITY["policy_rate"] * rate_delta
    if snap.supply_gap_pct is not None:
        # A deficit pushes crude up, which reaches equities through rule 1.
        contrib["supply_balance"] = ELASTICITY["supply_deficit"] * snap.supply_gap_pct
    cagr = snap.nifty_cagr_pct if snap.nifty_cagr_pct is not None else snap.sensex_cagr_pct
    if cagr is not None:
        contrib["cagr"] = ELASTICITY["cagr"] * (cagr - CAGR_BASELINE_PCT)
    if snap.acquisition_score:
        contrib["acquisitions"] = ELASTICITY["acquisition"] * _clamp(snap.acquisition_score)
    if snap.policy_tone:
        contrib["policy_tone"] = ELASTICITY["policy_tone"] * _clamp(snap.policy_tone)
    if snap.insider_tone:
        contrib["insider"] = ELASTICITY["insider"] * _clamp(snap.insider_tone)

    # ── Per-index net (tilted) ─────────────────────────────────────────────
    net: Dict[str, float] = {}
    for index, tilt in INDEX_TILT.items():
        total = 0.0
        for channel, value in contrib.items():
            total += value * tilt.get(_TILT_GROUP.get(channel, ""), 1.0)
        net[index] = round(max(-MAX_TOTAL_PCT, min(MAX_TOTAL_PCT, total)), 4)

    # ── Confidence ─────────────────────────────────────────────────────────
    coverage = snap.coverage()
    confidence = coverage
    if snap.stale:
        confidence *= 0.75
    if prev.asof == "" or (prev.crude_usd is None and prev.gold_usd is None):
        # No previous point: every change is unknown, so the graph has nothing
        # to differentiate. Keep it low rather than pretend.
        confidence *= 0.35
        missing.append("previous_snapshot")
    confidence = round(max(0.0, min(1.0, confidence)), 3)

    # ── Regime label ───────────────────────────────────────────────────────
    regime = _classify_regime(crude_pct, gold_pct, dxy_pct, infl_gap, cpi_delta)

    # ── Rule audit ─────────────────────────────────────────────────────────
    audit: List[Dict[str, Any]] = []
    audit.append(_audit_pair(
        1, "Crude up -> dollar down, equities down",
        crude_pct, dxy_pct, extra_ok=(contrib.get("crude", 0.0) <= 0),
    ))
    if infl_gap is not None:
        observed = (
            f"CPI {snap.cpi_yoy_pct:.2f}% vs {snap.cpi_target_pct:.1f}% target "
            f"({infl_gap:+.2f}pp); valuation channel {contrib.get('inflation_valuation', 0):+.3f}%"
        )
        status = "CONFIRMED" if infl_gap > 0 and contrib.get("inflation_valuation", 0) <= 0 else (
            "CONTRADICTED" if infl_gap > 0 else "BELOW TARGET"
        )
        audit.append(_rule(2, "Inflation up -> rates up -> equities down (Fed/RBI channel)", observed, status))
    else:
        audit.append(_rule(2, "Inflation up -> rates up -> equities down", "CPI not observed", "INSUFFICIENT DATA"))
    audit.append(_audit_pair(
        3, "Gold up -> dollar down, equities down",
        gold_pct, dxy_pct, extra_ok=(contrib.get("gold", 0.0) <= 0),
    ))
    if crude_pct is not None and gold_pct is not None:
        same = (crude_pct > 0) == (gold_pct > 0)
        audit.append(_rule(
            4,
            "Crude is directly proportional to gold under the Fed-money framing",
            f"crude {crude_pct:+.2f}% vs gold {gold_pct:+.2f}%",
            "CONFIRMED" if same else "DIVERGING",
        ))
    else:
        audit.append(_rule(4, "Crude proportional to gold", "missing crude/gold", "INSUFFICIENT DATA"))
    if snap.supply_gap_pct is not None:
        audit.append(_rule(
            5,
            "Crude also reflects global supply balance (supply vs demand)",
            f"supply gap {snap.supply_gap_pct:+.2f}% (positive = deficit)",
            "WIRED",
        ))
    else:
        audit.append(_rule(5, "Crude reflects global supply balance", "supply_gap_pct not set", "INSUFFICIENT DATA"))
    audit.append(_audit_pair(
        6, "Gold up with inflation, against dollar and equities (when unopposed)",
        gold_pct, dxy_pct, extra_ok=(contrib.get("gold", 0.0) <= 0),
    ))
    audit.append(_rule(
        7, "CPI is a primary inflation input",
        f"CPI {snap.cpi_yoy_pct}" if snap.cpi_yoy_pct is not None else "CPI not observed",
        "WIRED" if snap.cpi_yoy_pct is not None else "INSUFFICIENT DATA",
    ))
    audit.append(_rule(
        8, "CAGR is an equity-growth channel",
        f"{cagr:.2f}% vs {CAGR_BASELINE_PCT:.0f}% baseline" if cagr is not None else "CAGR not observed",
        "WIRED" if cagr is not None else "INSUFFICIENT DATA",
    ))
    audit.append(_rule(
        9, "Acquisitions add breadth / risk appetite",
        f"deal score {snap.acquisition_score:+.2f}",
        "WIRED" if snap.acquisition_score else "NO DEAL FLOW",
    ))
    audit.append(_rule(
        10, "IMF / World Bank / RBI / Fed tone",
        f"policy tone {snap.policy_tone:+.2f}",
        "WIRED" if snap.policy_tone else "NO POLICY ITEMS",
    ))
    audit.append(_rule(
        11, "Multi-source aggregation feeds this graph",
        f"coverage {coverage:.0%} across {len(snap.provenance)} sources",
        "WIRED" if coverage >= 0.5 else "PARTIAL",
    ))

    return {
        "schema": SCHEMA_VERSION,
        "asof": snap.asof,
        "fed_framing": bool(fed_framing),
        "regime": regime,
        "coverage": round(coverage, 3),
        "confidence": confidence,
        "apply": bool(confidence >= MIN_CONFIDENCE_TO_APPLY),
        "missing": sorted(set(missing)),
        "changes": {
            "crude_pct": _r(crude_pct), "gold_pct": _r(gold_pct),
            "dxy_pct": _r(dxy_pct), "usdinr_pct": _r(inr_pct),
            "cpi_delta_pp": _r(cpi_delta), "policy_rate_delta_pp": _r(rate_delta),
            "supply_gap_pct": _r(snap.supply_gap_pct),
        },
        "levels": {
            "crude_usd": _r(snap.crude_usd), "gold_usd": _r(snap.gold_usd),
            "dxy": _r(snap.dxy), "usdinr": _r(snap.usdinr),
            "cpi_yoy_pct": _r(snap.cpi_yoy_pct), "cpi_target_pct": _r(snap.cpi_target_pct),
            "repo_rate_pct": _r(snap.repo_rate_pct), "fed_funds_pct": _r(snap.fed_funds_pct),
            "nifty_cagr_pct": _r(snap.nifty_cagr_pct), "sensex_cagr_pct": _r(snap.sensex_cagr_pct),
            "india_vix": _r(snap.india_vix),
        },
        "channel_contributions_pct": {k: round(v, 4) for k, v in contrib.items()},
        "net_pct": net,
        "narrative": _narrative(snap, contrib, net, regime, infl_gap),
        "rule_audit": audit,
        "provenance": dict(snap.provenance),
        "stale": snap.stale,
        "notes": list(snap.notes),
    }


def _r(value: Optional[float], nd: int = 4) -> Optional[float]:
    return None if value is None else round(float(value), nd)


def _audit_pair(
    rule: int,
    statement: str,
    a: Optional[float],
    b: Optional[float],
    extra_ok: bool = True,
) -> Dict[str, Any]:
    """Audit a stated *inverse* relationship (rule 1/3/6 all state "up -> down")."""
    if a is None or b is None:
        return _rule(rule, statement, "inputs missing", "INSUFFICIENT DATA")
    same = (a > 0) == (b > 0)
    ok = (not same) and extra_ok
    return _rule(
        rule,
        statement,
        f"{a:+.2f}% vs {b:+.2f}%",
        "CONFIRMED" if ok else "CONTRADICTED",
    )


def _classify_regime(
    crude: Optional[float],
    gold: Optional[float],
    dxy: Optional[float],
    infl_gap: Optional[float],
    cpi_delta: Optional[float],
) -> str:
    if crude is not None and crude > 2 and gold is not None and gold > 1:
        return "COMMODITY_INFLATION"
    if infl_gap is not None and infl_gap > 0.5 and cpi_delta is not None and cpi_delta > 0:
        return "STAGFLATION_PRESSURE"
    if dxy is not None and dxy > 0.5:
        return "DOLLAR_TIGHTENING"
    if crude is not None and crude < -2:
        return "DISINFLATIONARY"
    return "NEUTRAL_MACRO"


def _narrative(
    snap: MacroSnapshot,
    contrib: Dict[str, float],
    net: Dict[str, float],
    regime: str,
    infl_gap: Optional[float],
) -> List[str]:
    lines: List[str] = [f"Macro regime: {regime.replace('_', ' ').title()}."]
    if snap.crude_usd is not None:
        lines.append(f"Brent at ${snap.crude_usd:,.2f}; crude channel {contrib.get('crude', 0.0):+.3f}%.")
    if snap.gold_usd is not None:
        lines.append(f"Gold at ${snap.gold_usd:,.2f}; gold channel {contrib.get('gold', 0.0):+.3f}%.")
    if snap.dxy is not None:
        lines.append(f"DXY at {snap.dxy:,.2f}; dollar channel {contrib.get('dxy', 0.0):+.3f}%.")
    if infl_gap is not None:
        lines.append(
            f"CPI {snap.cpi_yoy_pct:.2f}% is {infl_gap:+.2f}pp versus the "
            f"{snap.cpi_target_pct:.1f}% target; valuation channel "
            f"{contrib.get('inflation_valuation', 0.0):+.3f}%."
        )
    if snap.supply_gap_pct is not None:
        side = "deficit" if snap.supply_gap_pct > 0 else "surplus"
        lines.append(f"Global oil balance shows a {abs(snap.supply_gap_pct):.2f}% {side}.")
    if snap.acquisition_score:
        lines.append(f"Acquisition breadth score {snap.acquisition_score:+.2f}.")
    if snap.policy_tone:
        lines.append(f"IMF/World Bank/RBI/Fed tone {snap.policy_tone:+.2f}.")
    nifty = net.get("NIFTY 50", 0.0)
    lines.append(
        f"Net macro tilt: NIFTY {nifty:+.3f}%, BANKNIFTY {net.get('BANKNIFTY', 0.0):+.3f}%, "
        f"SENSEX {net.get('SENSEX', 0.0):+.3f}%. This is a tilt, not a forecast."
    )
    return lines


# ═══════════════════════════════════════════════════════════════════════════
#  Prediction integration
# ═══════════════════════════════════════════════════════════════════════════

def apply_macro_overlay(
    matrix: Dict[str, Any],
    snapshot: Optional[MacroSnapshot] = None,
    prev: Optional[MacroSnapshot] = None,
) -> Dict[str, Any]:
    """Attach the macro block to a prediction matrix and nudge its legs.

    * ``pred_open`` / ``pred_close`` shift by the net macro percent.
    * ``pred_high`` / ``pred_low`` shift and widen symmetrically by a fraction
      of |net|, because fresh macro information is a variance event.
    * Everything is clamped inside the matrix's own prediction clamp so the
      macro layer can never push a level outside the configured band.

    The input matrix is never mutated. If confidence is too low, the block is
    attached for transparency but no level is moved.
    """
    if not isinstance(matrix, dict) or not matrix:
        return matrix
    try:
        snap = snapshot or fetch_snapshot(allow_network=True)
        analysis = analyze(snap, prev if prev is not None else load_last_snapshot())
    except Exception as exc:  # never break the prediction core
        out = dict(matrix)
        out["macro"] = {"schema": SCHEMA_VERSION, "error": str(exc), "apply": False}
        return out

    out = dict(matrix)
    out["macro"] = analysis
    if not analysis["apply"]:
        return out

    from config import MAX_PREDICTION_RANGE  # local import keeps module import-light

    for index, pct in analysis["net_pct"].items():
        data = out.get(index)
        if not isinstance(data, dict) or "error" in data:
            continue
        try:
            spot = float(data.get("prev_close") or 0.0)
        except (TypeError, ValueError):
            continue
        if spot <= 0:
            continue
        shift = pct / 100.0 * spot
        widen = abs(shift) * 0.5
        cap = float(MAX_PREDICTION_RANGE.get(index, 2000))

        new = dict(data)
        # open/close shift with the level; high/low also widen by half the
        # shift, because fresh macro information is a variance event.
        for leg, widen_sign in (("pred_open", 0), ("pred_close", 0),
                                ("pred_high", 1), ("pred_low", -1)):
            if new.get(leg) is None:
                continue
            try:
                val = float(new[leg]) + shift + widen_sign * widen
            except (TypeError, ValueError):
                continue
            val = max(spot - cap, min(spot + cap, val))
            new[leg] = round(val, 2)
        new["macro_shift_points"] = round(shift, 1)
        new["macro_regime"] = analysis["regime"]
        out[index] = new

    return out
