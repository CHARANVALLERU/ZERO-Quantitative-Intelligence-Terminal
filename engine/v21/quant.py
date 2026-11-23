"""
Quant maths from the MIT / Stanford course material, implemented (item 12).

Covered: bond price and duration (Macaulay/modified), the mean-variance frontier,
PCA common factors ("stocks move together"), a Black–Scholes pricer, and a solved
convex rebalancing problem (minimum variance subject to a target return and a
position cap).

Everything is numpy-only so it runs inside the Streamlit process without SciPy.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

# ── Indicators ──────────────────────────────────────────────────────────────


def ema(values, span: int) -> np.ndarray:
    """Exponential moving average of an array, seeded on the first value."""
    out = np.asarray(values, dtype=float).copy()
    alpha = 2.0 / (span + 1.0)
    for i in range(1, len(out)):
        out[i] = alpha * out[i] + (1 - alpha) * out[i - 1]
    return out


# ── Bonds ───────────────────────────────────────────────────────────────────


def bond_price(face: float, coupon_rate: float, years: float, ytm: float,
               freq: int = 2) -> float:
    """Dirty price of a plain vanilla bond, coupons ``freq`` times a year."""
    n = int(round(years * freq))
    c = face * coupon_rate / freq
    r = ytm / freq
    if n <= 0:
        return float(face)
    pv_coupons = sum(c / (1 + r) ** k for k in range(1, n + 1))
    pv_face = face / (1 + r) ** n
    return float(pv_coupons + pv_face)


def bond_duration(face: float, coupon_rate: float, years: float, ytm: float,
                  freq: int = 2) -> Dict[str, float]:
    """Macaulay duration, modified duration and convexity."""
    n = int(round(years * freq))
    c = face * coupon_rate / freq
    r = ytm / freq
    if n <= 0:
        return {"macaulay": 0.0, "modified": 0.0, "convexity": 0.0, "price": float(face)}
    flows = [(k, c) for k in range(1, n + 1)]
    flows[-1] = (n, c + face)
    price = sum(cf / (1 + r) ** k for k, cf in flows)
    mac = sum((k / freq) * cf / (1 + r) ** k for k, cf in flows) / price
    mod = mac / (1 + r)
    conv = sum(k * (k + 1) * cf / (1 + r) ** (k + 2) for k, cf in flows) / price / (freq ** 2)
    return {"macaulay": float(mac), "modified": float(mod),
            "convexity": float(conv), "price": float(price)}


def bond_suggestion(face: float, coupon_rate: float, years: float, ytm: float,
                    expected_yield_change_bp: float = 0.0) -> Dict[str, Any]:
    """Turn duration into a rate-view trade idea."""
    d = bond_duration(face, coupon_rate, years, ytm)
    dy = expected_yield_change_bp / 10_000.0
    price_change_pct = -d["modified"] * dy * 100.0
    bias = "BUY" if expected_yield_change_bp < 0 else ("SELL" if expected_yield_change_bp > 0 else "HOLD")
    return {
        "engine": "bond",
        "available": True,
        "label": f"{years:g}Y {coupon_rate*100:g}% · mod dur {d['modified']:.2f}",
        "bias": bias,
        "confidence": 0.6,
        "detail": (f"A {expected_yield_change_bp:+.0f}bp move changes price "
                   f"{price_change_pct:+.2f}% at modified duration {d['modified']:.2f}."),
        "metrics": {**d, "expected_yield_change_bp": expected_yield_change_bp,
                    "price_change_pct": round(price_change_pct, 3)},
    }


# ── Portfolios ──────────────────────────────────────────────────────────────


def mean_variance_frontier(expected_returns: Sequence[float],
                           cov: Sequence[Sequence[float]],
                           n_points: int = 20,
                           risk_free: float = 0.065) -> Dict[str, Any]:
    """Long-only frontier by weighted sampling of the simplex (no SciPy needed)."""
    mu = np.asarray(expected_returns, dtype=float)
    S = np.asarray(cov, dtype=float)
    n = len(mu)
    if n < 2 or S.shape != (n, n):
        return {"available": False, "reason": "need a mean vector and matching covariance"}

    rng = np.random.default_rng(11)
    best = {"sharpe": -np.inf}
    points: List[Dict[str, Any]] = []
    for _ in range(4000):
        w = rng.dirichlet(np.ones(n))
        ret = float(w @ mu)
        vol = float(math.sqrt(max(w @ S @ w, 0.0)))
        sharpe = (ret - risk_free) / vol if vol > 0 else 0.0
        points.append({"ret": ret, "vol": vol, "sharpe": sharpe})
        if sharpe > best["sharpe"]:
            best = {"sharpe": sharpe, "ret": ret, "vol": vol, "weights": [round(float(x), 4) for x in w]}

    points.sort(key=lambda p: p["vol"])
    step = max(1, len(points) // n_points)
    frontier = [{"vol": round(p["vol"], 5), "ret": round(p["ret"], 5)}
                for p in points[::step][:n_points]]
    return {
        "available": True,
        "frontier": frontier,
        "tangency": {"ret": round(best["ret"], 5), "vol": round(best["vol"], 5),
                     "sharpe": round(best["sharpe"], 3), "weights": best["weights"]},
        "n_assets": n,
    }


def pca_factors(returns: Sequence[Sequence[float]], n_components: int = 3) -> Dict[str, Any]:
    """PCA of a returns matrix (rows = assets, cols = time) → common factors."""
    R = np.asarray(returns, dtype=float)
    if R.ndim != 2 or R.shape[0] < 2 or R.shape[1] < 20:
        return {"available": False, "reason": "need >= 2 assets and >= 20 observations"}
    X = R - R.mean(axis=1, keepdims=True)
    # SVD on the covariance keeps the sign convention of the leading factor stable.
    C = np.cov(X)
    vals, vecs = np.linalg.eigh(C)
    order = np.argsort(vals)[::-1][:n_components]
    vals, vecs = vals[order], vecs[:, order]
    total = float(np.sum(np.maximum(vals, 0)) or 1.0)
    return {
        "available": True,
        "explained_variance": [round(float(v / total), 4) for v in vals],
        "cumulative": round(float(np.sum(vals) / total), 4),
        "loadings": [[round(float(x), 4) for x in vecs[:, i]] for i in range(len(vals))],
        "n_assets": int(R.shape[0]),
        "n_obs": int(R.shape[1]),
        "engine": "pca",
    }


def black_scholes(spot: float, strike: float, years: float, vol: float,
                  rate: float = 0.065, kind: str = "call") -> Dict[str, float]:
    """Closed-form price + greeks."""
    if spot <= 0 or strike <= 0 or years <= 0 or vol <= 0:
        return {"price": 0.0, "delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    from math import erf, exp, log, sqrt

    def N(x: float) -> float:
        return 0.5 * (1.0 + erf(x / sqrt(2.0)))

    def n(x: float) -> float:
        return exp(-0.5 * x * x) / sqrt(2.0 * math.pi)

    d1 = (log(spot / strike) + (rate + 0.5 * vol * vol) * years) / (vol * sqrt(years))
    d2 = d1 - vol * sqrt(years)
    disc = exp(-rate * years)
    if kind == "call":
        price = spot * N(d1) - strike * disc * N(d2)
        delta = N(d1)
        theta = (-spot * n(d1) * vol / (2 * sqrt(years)) - rate * strike * disc * N(d2)) / 365
    else:
        price = strike * disc * N(-d2) - spot * N(-d1)
        delta = N(d1) - 1.0
        theta = (-spot * n(d1) * vol / (2 * sqrt(years)) + rate * strike * disc * N(-d2)) / 365
    return {
        "price": round(float(price), 4),
        "delta": round(float(delta), 4),
        "gamma": round(float(n(d1) / (spot * vol * sqrt(years))), 8),
        "vega": round(float(spot * n(d1) * sqrt(years) / 100), 4),
        "theta": round(float(theta), 4),
    }


def _project_capped_simplex(v: np.ndarray, cap: float) -> np.ndarray:
    """
    Euclidean projection onto {w : sum w = 1, 0 <= w_i <= cap}.

    Bisection on the dual variable tau: ``w = clip(v - tau, 0, cap)``. Normalising
    a clipped vector afterwards (the naive approach) can push a weight back above
    the cap, which is exactly the infeasible result this replaces.
    """
    lo = float(np.min(v)) - 1.0
    hi = float(np.max(v)) - cap + 1.0
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if np.clip(v - mid, 0.0, cap).sum() > 1.0:
            lo = mid
        else:
            hi = mid
    w = np.clip(v - 0.5 * (lo + hi), 0.0, cap)
    s = float(w.sum())
    if s <= 0:
        return np.full(len(v), 1.0 / len(v))
    # Bisection already lands on sum(w)=1; a rescale here would re-break the cap.
    return w if abs(s - 1.0) < 1e-6 else w / s


def convex_rebalance(cov: Sequence[Sequence[float]],
                     target_return: Optional[float] = None,
                     mu: Optional[Sequence[float]] = None,
                     max_weight: float = 0.35,
                     iters: int = 4000) -> Dict[str, Any]:
    """
    Solve the constrained minimum-variance problem by projected gradient descent.

        min  wᵀΣw   s.t. Σw = 1, 0 ≤ w_i ≤ max_weight, (w·μ = target)

    Returns weights plus the achieved vol/return, so the panel can show the
    objective value rather than trusting the solver.
    """
    S = np.asarray(cov, dtype=float)
    n = S.shape[0]
    if n < 2 or S.shape != (n, n):
        return {"available": False, "reason": "covariance must be square and >= 2x2"}
    if max_weight * n < 1.0:
        return {"available": False, "reason": "max_weight infeasible for this many assets"}

    w = np.full(n, 1.0 / n)
    lr = 1.0 / (2.0 * float(np.max(np.linalg.eigvalsh(S))) + 1e-9)
    for _ in range(iters):
        grad = 2.0 * S @ w
        w = w - lr * grad
        if target_return is not None and mu is not None:
            m = np.asarray(mu, dtype=float)
            gap = float(w @ m) - target_return
            w = w - gap * m / max(float(m @ m), 1e-12)
        w = _project_capped_simplex(w, max_weight)

    vol = float(math.sqrt(max(w @ S @ w, 0.0)))
    out = {
        "available": True,
        "weights": [round(float(x), 4) for x in w],
        "vol": round(vol, 6),
        "concentration": round(float(np.sum(w ** 2)), 4),
        "objective": round(float(w @ S @ w), 8),
        "max_weight": max_weight,
    }
    if mu is not None:
        out["expected_return"] = round(float(w @ np.asarray(mu, dtype=float)), 6)
    return out
