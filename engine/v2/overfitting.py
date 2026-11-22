"""
engine.v2.overfitting — PBO (CSCV) and the Deflated Sharpe Ratio.
=================================================================

The lab generated 20 specs, ran one backtest each on 10 years of daily bars,
and kept whatever cleared a fixed gate (PF ≥ 1.15, DD ≤ 12%, ≥ 20 trades).
That gate says nothing about **selection**: with 20 candidates, the best one is
expected to look good even when none has an edge. This module supplies the two
standard corrections:

* **PBO** (Probability of Backtest Overfitting) via combinatorially symmetric
  cross-validation (Bailey, Borwein, López de Prado & Zhu, 2017). Split the
  return matrix into S blocks, use every S/2-block subset as in-sample, pick the
  IS-best configuration, and count how often it lands in the bottom half
  out-of-sample. PBO near 0.5 = the selection is a coin flip.
* **Deflated Sharpe Ratio** (Bailey & López de Prado, 2014): the probability
  that the observed Sharpe exceeds what the best of N trials would produce by
  chance, given the trials' Sharpe dispersion, skew and kurtosis.

Pure Python + math (no scipy). Works on plain lists so it is testable offline.
"""
from __future__ import annotations

import itertools
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "probabilistic_sharpe",
    "expected_max_sharpe",
    "deflated_sharpe",
    "pbo_cscv",
    "summarise",
]

_EULER_GAMMA = 0.5772156649015329


# ── Normal distribution helpers (no scipy) ──────────────────────────────────

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    """Acklam's rational approximation, |error| < 1.15e-9."""
    if p <= 0.0:
        return -math.inf
    if p >= 1.0:
        return math.inf
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


# ── Moments ─────────────────────────────────────────────────────────────────

def _moments(xs: Sequence[float]) -> Tuple[int, float, float, float, float]:
    """n, mean, std (sample), skew, excess kurtosis."""
    n = len(xs)
    if n < 2:
        return n, 0.0, 0.0, 0.0, 0.0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var) if var > 0 else 0.0
    if sd <= 0:
        return n, m, 0.0, 0.0, 0.0
    skew = (n / ((n - 1) * (n - 2))) * sum(((x - m) / sd) ** 3 for x in xs)
    kurt = (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3))) * sum(((x - m) / sd) ** 4 for x in xs) \
        - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3))
    return n, m, sd, skew, kurt


def _sharpe(xs: Sequence[float], periods_per_year: float = 252.0) -> float:
    n, m, sd, _, _ = _moments(xs)
    if n < 3 or sd <= 1e-12:
        return 0.0
    return (m / sd) * math.sqrt(periods_per_year)


# ── DSR ─────────────────────────────────────────────────────────────────────

def expected_max_sharpe(n_trials: int, sharpe_std: float) -> float:
    """E[max of N iid Sharpe estimates] under the null (Gumbel approximation)."""
    if n_trials < 2 or sharpe_std <= 0:
        return 0.0
    n = float(n_trials)
    z1 = _norm_ppf(1.0 - 1.0 / n)
    z2 = _norm_ppf(1.0 - 1.0 / (n * math.e))
    return sharpe_std * ((1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2)


def probabilistic_sharpe(observed_sr: float, n_obs: int, skew: float = 0.0,
                         kurtosis: float = 0.0, benchmark_sr: float = 0.0) -> float:
    """PSR: P(true SR > benchmark | observed SR, skew, kurtosis, sample size)."""
    if n_obs < 3:
        return 0.0
    denom = 1.0 - skew * observed_sr + ((kurtosis) / 4.0) * observed_sr ** 2
    if denom <= 0:
        return 0.0
    z = (observed_sr - benchmark_sr) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return _norm_cdf(z)


def deflated_sharpe(returns: Sequence[float], n_trials: int,
                    sharpe_std: Optional[float] = None,
                    periods_per_year: float = 252.0) -> Dict[str, Any]:
    """
    DSR for the *selected* strategy's return series.

    ``n_trials`` is how many configurations were searched to find it (each
    parameter grid point counts). ``sharpe_std`` is the dispersion of the trials'
    annualised Sharpes; when omitted it is estimated as ``1/sqrt(T/years)``,
    the standard error of a zero-Sharpe estimate, which is the conservative
    choice when the search space's variance is unknown.
    """
    xs = [float(x) for x in returns]
    n, m, sd, skew, kurt = _moments(xs)
    sr = _sharpe(xs, periods_per_year)
    years = max(1e-9, n / periods_per_year)
    sr_std = sharpe_std if sharpe_std is not None else 1.0 / math.sqrt(max(1.0, years))
    sr0 = expected_max_sharpe(int(n_trials), sr_std)
    dsr = probabilistic_sharpe(sr, n, skew, kurt, benchmark_sr=sr0)
    return {
        "sharpe": round(sr, 3),
        "n_obs": n,
        "n_trials": int(n_trials),
        "benchmark_sharpe": round(sr0, 3),
        "deflated_sharpe": round(dsr, 4),
        "skew": round(skew, 3),
        "excess_kurtosis": round(kurt, 3),
        "significant": bool(dsr >= 0.95 and sr > 0),
    }


# ── PBO / CSCV ──────────────────────────────────────────────────────────────

def pbo_cscv(returns_by_trial: Dict[str, Sequence[float]], blocks: int = 8,
             max_combinations: int = 70) -> Dict[str, Any]:
    """
    Probability of Backtest Overfitting via CSCV.

    ``returns_by_trial`` maps a trial id to its per-period return series; all
    trials must share the same period index (same length), otherwise the
    comparison is not like-for-like. Longer series are truncated to the common
    length from the end.

    Returns ``{"pbo", "n_trials", "blocks", "combinations", "median_oos_rank"}``
    where a rank of 0 is the worst trial and 1 the best. ``pbo`` near 0.5 means
    the in-sample winner is no better than a coin flip out-of-sample.
    """
    trials = {k: [float(x) for x in v] for k, v in returns_by_trial.items() if v}
    if len(trials) < 2:
        return {"pbo": None, "reason": "need at least 2 trials", "n_trials": len(trials)}
    length = min(len(v) for v in trials.values())
    if length < blocks * 2:
        return {"pbo": None, "reason": f"need >= {blocks * 2} periods", "n_trials": len(trials)}

    ids = list(trials.keys())
    matrix = {k: v[-length:] for k, v in trials.items()}

    edges = [round(length * i / blocks) for i in range(blocks + 1)]
    block_slices = [(edges[i], edges[i + 1]) for i in range(blocks)]

    half = blocks // 2
    combos = list(itertools.combinations(range(blocks), half))
    if len(combos) > max_combinations:
        step = max(1, len(combos) // max_combinations)
        combos = combos[::step][:max_combinations]

    overfit = 0
    ranks: List[float] = []
    for is_blocks in combos:
        is_set = set(is_blocks)
        oos_blocks = [b for b in range(blocks) if b not in is_set]

        def _slice(sel: List[int], key: str) -> List[float]:
            out: List[float] = []
            for b in sel:
                s, e = block_slices[b]
                out.extend(matrix[key][s:e])
            return out

        is_scores = {k: _sharpe(_slice(list(is_blocks), k)) for k in ids}
        oos_scores = {k: _sharpe(_slice(oos_blocks, k)) for k in ids}

        best = max(ids, key=lambda k: is_scores[k])
        ordered = sorted(ids, key=lambda k: oos_scores[k])
        rank = ordered.index(best) / max(1, len(ordered) - 1)
        ranks.append(rank)
        if rank < 0.5:
            overfit += 1

    ranks_sorted = sorted(ranks)
    median_rank = ranks_sorted[len(ranks_sorted) // 2] if ranks_sorted else 0.0
    return {
        "pbo": round(overfit / len(combos), 4) if combos else None,
        "n_trials": len(ids),
        "periods": length,
        "blocks": blocks,
        "combinations": len(combos),
        "median_oos_rank": round(median_rank, 3),
        "verdict": (
            "selection is informative" if combos and overfit / len(combos) < 0.3
            else "selection is weak" if combos and overfit / len(combos) < 0.5
            else "selection is not better than chance"
        ),
    }


def summarise(returns_by_trial: Dict[str, Sequence[float]],
              selected: Optional[str] = None,
              blocks: int = 8) -> Dict[str, Any]:
    """PBO + DSR for a lab run, in one dict (what the lab gate consumes)."""
    pbo = pbo_cscv(returns_by_trial, blocks=blocks)
    ids = list(returns_by_trial.keys())
    if not ids:
        return {"pbo": pbo, "dsr": None}

    if selected is None:
        selected = max(ids, key=lambda k: _sharpe([float(x) for x in returns_by_trial[k]]))
    sharpes = [_sharpe([float(x) for x in returns_by_trial[k]]) for k in ids]
    mean_sr = sum(sharpes) / len(sharpes)
    sr_std = math.sqrt(sum((s - mean_sr) ** 2 for s in sharpes) / max(1, len(sharpes) - 1)) \
        if len(sharpes) > 1 else None
    dsr = deflated_sharpe(returns_by_trial[selected], n_trials=max(2, len(ids)),
                          sharpe_std=sr_std)
    return {"pbo": pbo, "dsr": dsr, "selected": selected,
            "trial_sharpe_dispersion": round(sr_std, 4) if sr_std else None}
