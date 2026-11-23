"""
Gamma exposure (GEX) — where the dealers' hedge is waiting.

Model
-----
Every option a customer buys has a seller on the other side. Market makers who
are net short options must delta-hedge: they buy when spot rises and sell when it
falls (short gamma), which *widens* moves, or they do the opposite (long gamma),
which *damps* them. Per strike:

    GEX_strike = (OI_call·Γ_call − OI_put·Γ_put) · spot² · 0.01 · sign

``Γ`` is the Black–Scholes gamma from the chain's own IV, ``spot²·0.01`` converts
it to the notional change per 1% move, and the sign convention is *dealer
perspective*: calls sold to customers leave dealers short call gamma, so the
customer-side convention used everywhere in the trading press is
``+OI_call·Γ`` (call wall = green) and ``−OI_put·Γ`` (put wall = red).

* ``net GEX > 0`` → dealers are long gamma → they **sell rallies and buy dips**:
  realised volatility is suppressed, price tends to range around the walls,
  premium decays. Fade extremes; respect the range.
* ``net GEX < 0`` → dealers are short gamma → they **chase**: hedging amplifies
  moves, swings get faster and wider. Momentum has room; do not fade blindly.
* The strike where cumulative GEX changes sign is the **gamma flip**. Price above
  the flip with positive GEX = pinning regime; price below the flip with negative
  GEX = acceleration regime.

Everything here is computed from live NSE chain data. If the chain cannot be
fetched the caller gets ``available: False`` — never a made-up profile.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Dict, List, Optional, Tuple

RISK_FREE = 0.065          # India 91-day T-bill, approximate
CONTRACT_MULTIPLIER = 1.0  # GEX is reported in index points of notional

# NSE publishes equity/index F&O expiry on Tuesday (since 2025) and some indices
# weekly. We read the expiry from the payload; this is only a fallback.
_DEFAULT_DAYS = 3.0


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_gamma(spot: float, strike: float, iv: float, years: float) -> float:
    """Black–Scholes gamma (per 1 unit of spot)."""
    if spot <= 0 or strike <= 0 or iv <= 0 or years <= 0:
        return 0.0
    vol_sqrt_t = iv * math.sqrt(years)
    if vol_sqrt_t <= 0:
        return 0.0
    d1 = (math.log(spot / strike) + (RISK_FREE + 0.5 * iv * iv) * years) / vol_sqrt_t
    return _norm_pdf(d1) / (spot * vol_sqrt_t)


def _years_to(expiry: Any) -> float:
    """Calendar time to expiry in years, floored at half a trading day."""
    if expiry is None:
        return _DEFAULT_DAYS / 365.0
    try:
        if isinstance(expiry, (int, float)):
            exp = _dt.datetime.fromtimestamp(float(expiry))
        else:
            exp = _dt.datetime.strptime(str(expiry).strip()[:10], "%d-%b-%Y")
    except Exception:
        try:
            exp = _dt.datetime.fromisoformat(str(expiry)[:10])
        except Exception:
            return _DEFAULT_DAYS / 365.0
    now = _dt.datetime.now()
    days = (exp - now).total_seconds() / 86400.0
    return max(days, 0.5) / 365.0


def _parse_chain(payload: Dict[str, Any]) -> Tuple[float, Any, List[Dict[str, Any]]]:
    """Pull spot, expiry and per-strike CE/PE rows out of the NSE payload."""
    records = (payload or {}).get("records", {}) or {}
    rows = (payload or {}).get("filtered", {}).get("data") or records.get("data") or []
    spot = records.get("underlyingValue") or 0.0
    expiries = records.get("expiryDates") or []
    expiry = expiries[0] if expiries else None
    if expiry:
        rows = [r for r in rows if r.get("expiry") == expiry] or rows
    return float(spot or 0.0), expiry, rows


def compute_gex(payload: Dict[str, Any], spot: Optional[float] = None) -> Dict[str, Any]:
    """
    Build the strike-by-strike GEX profile from a raw NSE option-chain payload.

    Returns a dict with ``available``, ``profile`` (list of strikes), ``totals``,
    ``gamma_flip``, ``regime``, ``playbook`` and ``suggestion``.
    """
    spot_v, expiry, rows = _parse_chain(payload)
    spot_v = float(spot or spot_v or 0.0)
    if not rows:
        return {"available": False, "reason": "no option-chain rows"}
    if spot_v <= 0:
        return {"available": False, "reason": "no underlying spot in payload"}

    years = _years_to(expiry)
    profile: List[Dict[str, Any]] = []
    for r in rows:
        strike = r.get("strikePrice")
        if strike is None:
            continue
        ce = r.get("CE") or {}
        pe = r.get("PE") or {}
        iv_ce = float(ce.get("impliedVolatility") or 0) / 100.0
        iv_pe = float(pe.get("impliedVolatility") or 0) / 100.0
        oi_ce = float(ce.get("openInterest") or 0)
        oi_pe = float(pe.get("openInterest") or 0)
        g_ce = bs_gamma(spot_v, float(strike), iv_ce, years)
        g_pe = bs_gamma(spot_v, float(strike), iv_pe, years)
        scale = spot_v * spot_v * 0.01 * CONTRACT_MULTIPLIER
        call_gex = oi_ce * g_ce * scale
        put_gex = -oi_pe * g_pe * scale
        profile.append({
            "strike": float(strike),
            "call_gex": call_gex,
            "put_gex": put_gex,
            "net_gex": call_gex + put_gex,
            "call_oi": oi_ce,
            "put_oi": oi_pe,
            "iv_call": round(iv_ce * 100, 2),
            "iv_put": round(iv_pe * 100, 2),
        })

    profile.sort(key=lambda x: x["strike"])
    if not profile:
        return {"available": False, "reason": "chain had no strikes"}

    total = sum(p["net_gex"] for p in profile)
    call_wall = max(profile, key=lambda p: p["call_gex"])
    put_wall = min(profile, key=lambda p: p["put_gex"])

    # Gamma flip: interpolate the zero crossing of the *cumulative* profile.
    flip: Optional[float] = None
    cum = 0.0
    prev = None
    for p in profile:
        cum += p["net_gex"]
        if prev is not None and (prev[1] < 0 <= cum or prev[1] > 0 >= cum):
            x0, y0 = prev[0], prev[1]
            x1, y1 = p["strike"], cum
            if y1 != y0:
                flip = x0 + (x1 - x0) * (-y0) / (y1 - y0)
            break
        prev = (p["strike"], cum)

    regime = "positive" if total > 0 else "negative"
    above_flip = flip is not None and spot_v >= flip
    if regime == "positive":
        playbook = ("Dealers are long gamma: they sell rallies and buy dips. "
                    "Expect pinning near the largest walls, compressed realised vol "
                    "and premium melt in a range. Fade extremes, do not chase breakouts.")
        structure = "range / mean reversion"
    else:
        playbook = ("Dealers are short gamma: hedging chases price, so swings get "
                    "faster and wider. Momentum has room to run; respect the swing "
                    "instead of fading it, and widen stops.")
        structure = "trend / acceleration"

    suggestion = _suggestion(regime, spot_v, flip, call_wall, put_wall, above_flip)

    return {
        "available": True,
        "spot": round(spot_v, 2),
        "expiry": str(expiry) if expiry else None,
        "days_to_expiry": round(years * 365, 1),
        "profile": profile,
        "totals": {
            "net_gex": total,
            "call_gex": sum(p["call_gex"] for p in profile),
            "put_gex": sum(p["put_gex"] for p in profile),
            "call_wall": call_wall["strike"],
            "call_wall_gex": call_wall["call_gex"],
            "put_wall": put_wall["strike"],
            "put_wall_gex": put_wall["put_gex"],
            "net_gex_bn": round(total / 1e9, 3),
        },
        "gamma_flip": round(flip, 2) if flip is not None else None,
        "regime": regime,
        "spot_vs_flip": ("above" if above_flip else "below") if flip else None,
        "market_structure": structure,
        "playbook": playbook,
        "suggestion": suggestion,
        "engine": "GEX",
    }


def _suggestion(regime: str, spot: float, flip: Optional[float],
                call_wall: Dict[str, Any], put_wall: Dict[str, Any],
                above_flip: bool) -> Dict[str, Any]:
    """Turn the regime into one labelled, falsifiable trade idea."""
    cw, pw = call_wall["strike"], put_wall["strike"]
    if regime == "positive":
        if spot > cw:
            idea = "fade into the call wall"
            action = "SELL"
            detail = (f"Spot {spot:.0f} is above the call wall {cw:.0f}; dealers sell "
                      f"into strength. Favour premium selling / range fades toward {cw:.0f}.")
        elif spot < pw:
            idea = "buy the put wall defence"
            action = "BUY"
            detail = (f"Spot {spot:.0f} is under the put wall {pw:.0f}; dealer hedging "
                      f"buys dips. Long gamma bounce is the higher-probability side.")
        else:
            idea = "range between walls"
            action = "RANGE"
            detail = (f"Spot {spot:.0f} sits between the put wall {pw:.0f} and call wall "
                      f"{cw:.0f}. Positive GEX pins price; sell the edges, expect decay.")
        confidence = 0.62
    else:
        if flip is not None and above_flip:
            idea = "ride momentum above the gamma flip"
            action = "BUY"
            detail = (f"Negative GEX and spot {spot:.0f} above the flip {flip:.0f}: hedging "
                      f"chases price. Trend continuation is favoured over fading.")
        elif flip is not None:
            idea = "ride momentum below the gamma flip"
            action = "SELL"
            detail = (f"Negative GEX and spot {spot:.0f} below the flip {flip:.0f}: dealers "
                      f"chase downside. Downside continuation, wide stops.")
        else:
            idea = "expect expansion"
            action = "VOL"
            detail = "Negative GEX with no clean flip: expect wider swings than the range implies."
        confidence = 0.58
    return {
        "bias": action,
        "idea": idea,
        "detail": detail,
        "confidence": confidence,
        "invalidation": (f"spot crossing {flip:.0f} (gamma flip)" if flip else "GEX sign flip"),
        "label": f"GEX {regime}",
    }


def live(index: str = "NIFTY") -> Dict[str, Any]:
    """Fetch the NSE chain and compute GEX. Network, therefore button-driven."""
    from engine.v21 import indices as idx

    key = idx.canonical(index)
    try:
        from data.options_chain import fetch_nse_option_chain
    except Exception as exc:  # pragma: no cover
        return {"available": False, "reason": f"options module unavailable: {exc}"}
    try:
        payload = fetch_nse_option_chain(idx.UNIVERSE[key]["nse"])
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"NSE chain fetch failed: {type(exc).__name__}"}
    if not payload:
        return {"available": False, "reason": "NSE returned no option chain (rate-limited or closed)"}
    q = idx.quote(key)
    out = compute_gex(payload, spot=q.get("last") if q.get("available") else None)
    out["index"] = key
    out["quote"] = q

    # Persist the chain so OI history survives the session (data/options_snapshot
    # was built for exactly this and previously had no caller). Failure to persist
    # must never break the read path.
    try:
        from data import options_snapshot
        options_snapshot.ingest_payload(payload, idx.UNIVERSE[key]["nse"])
        out["snapshot_saved"] = True
    except Exception as exc:  # noqa: BLE001
        out["snapshot_saved"] = False
        out["snapshot_error"] = f"{type(exc).__name__}: {exc}"
    return out


def hedge_ladder(gex: Dict[str, Any], top: int = 8) -> List[Dict[str, Any]]:
    """Largest |net GEX| strikes, for the green/red bar chart."""
    if not gex.get("available"):
        return []
    rows = sorted(gex["profile"], key=lambda p: abs(p["net_gex"]), reverse=True)[:top]
    return sorted(rows, key=lambda p: p["strike"])
