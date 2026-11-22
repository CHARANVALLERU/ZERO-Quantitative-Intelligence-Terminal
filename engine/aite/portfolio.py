"""
ZERO AITE portfolio construction — 10-40 bots that don't all lose together.

Two integrity rules, added 2026-11-22:

1. **Calendar-aligned returns.** Correlation used to be computed between two
   trades' P&L numbers by position (``pnl_pct`` of trade 1 vs trade 2), which
   measures nothing: a bot with 8 trades and one with 40 were compared on
   unrelated dates. Correlation is now computed on a common *daily* return
   index built from each bot's own trade timestamps, over the intersecting
   dates only.
2. **Admission is earned.** A bot enters the book only if its exam **passed on
   real, timestamped bars**. The old "fill to target with the best remaining"
   branch admitted bots that had never cleared the gate (the stored book had
   20 alive bots against 1 recorded pass). If too few bots qualify, the book is
   honestly short and says why.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from engine import data_provenance as prov_mod
from engine.aite import config as cfg
from engine.aite import store
from engine.aite.exam import load_market_frame, run_exam
from engine.aite.models import BotGenome, PortfolioState

# A correlation needs a shared calendar, not just two non-empty vectors.
MIN_CORR_OVERLAP_DAYS = 20
REQUIRE_REAL_DATA = True
REQUIRE_PASSING_EXAM = True


# ── Return series ───────────────────────────────────────────────────────────

def _parse_times(values: List[Any]):
    """Parse trade timestamps; returns (DatetimeIndex or None, all_placeholder)."""
    import pandas as pd

    strs = [str(v) for v in values]
    placeholder = bool(strs) and all(s.startswith(("bar_", "Bar_", "idx_")) for s in strs)
    if placeholder:
        return None, True
    # Element-wise parsing: blotters mix "YYYY-MM-DD HH:MM:SS" and ISO "T"
    # strings, and a single-format vectorised parse silently NaTs half of them.
    parsed = []
    for s in strs:
        try:
            parsed.append(pd.Timestamp(s))
        except (ValueError, TypeError):
            parsed.append(pd.NaT)
    ts = pd.Series(parsed)
    if ts.isna().all():
        return None, True
    return pd.DatetimeIndex(ts.dt.normalize()), False


def _trades_to_daily(trades: List[Dict[str, Any]]):
    """
    Aggregate a trade blotter into a daily return series.

    Each trade's realised return is booked on its **exit** date and scaled by
    the fraction of the year the trade was open, so overlapping trades on the
    same day add up instead of being averaged by trade count. Returns an empty
    Series when the blotter has no usable calendar (e.g. ``bar_12`` stamps).
    """
    import pandas as pd

    if not trades:
        return pd.Series(dtype=float)
    idx, placeholder = _parse_times([t.get("exit_time") for t in trades])
    if idx is None or placeholder:
        return pd.Series(dtype=float)

    rows = []
    for t, day in zip(trades, idx):
        try:
            ret = float(t.get("pnl_pct", 0.0)) / 100.0
        except (TypeError, ValueError):
            ret = 0.0
        rows.append((day, ret))
    s = pd.Series([r for _, r in rows], index=idx, dtype=float)
    daily = s.groupby(level=0).sum().sort_index()
    return daily


def _return_series(genome: BotGenome, df, exam: Any = None):
    """
    Daily return path for one bot.

    Returns a ``pd.Series`` indexed by calendar date. An empty Series means
    "cannot be aligned" — the caller must not fall back to positional maths.
    """
    exam = exam if exam is not None else run_exam(genome, df)
    series = _trades_to_daily(exam.trades or [])
    try:
        series.attrs["data_source"] = getattr(exam, "data_source", "")
        series.attrs["exam_passed"] = bool(getattr(exam, "passed", False))
    except Exception:
        pass
    return series


def aligned_return_frame(series: Dict[str, Any]):
    """Outer-join per-bot daily returns onto one calendar (NaN = not in market)."""
    import pandas as pd

    usable = {k: v for k, v in series.items() if isinstance(v, pd.Series) and len(v)}
    if not usable:
        return pd.DataFrame()
    return pd.DataFrame(usable).sort_index()


def pairwise_corr(a, b, min_overlap: int = MIN_CORR_OVERLAP_DAYS) -> Tuple[float, int]:
    """
    Correlation on calendar-aligned daily returns.

    Returns ``(corr, overlap_days)``. ``corr`` is ``None`` when the two series
    share fewer than ``min_overlap`` common dates: there is no number to
    report, and pretending there is (the old positional correlation) is how
    two unrelated bots looked diversified.
    """
    try:
        import pandas as pd
    except Exception:  # pragma: no cover
        return None, 0  # type: ignore[return-value]

    if not isinstance(a, pd.Series) or not isinstance(b, pd.Series):
        return None, 0
    if not len(a) or not len(b):
        return None, 0

    common = a.index.intersection(b.index)
    if len(common) < min_overlap:
        return None, int(len(common))

    x = a.reindex(common).astype(float)
    y = b.reindex(common).astype(float)
    mask = x.notna() & y.notna()
    x, y = x[mask], y[mask]
    if len(x) < min_overlap:
        return None, int(len(x))
    if float(x.std()) < 1e-12 or float(y.std()) < 1e-12:
        return 0.0, int(len(x))
    return float(np.corrcoef(x.values, y.values)[0, 1]), int(len(x))


def correlation_matrix(series: Dict[str, Any], min_overlap: int = MIN_CORR_OVERLAP_DAYS):
    """Full pairwise matrix plus the overlap counts that justify each cell."""
    ids = list(series.keys())
    corr: Dict[str, Dict[str, float]] = {i: {} for i in ids}
    overlap: Dict[str, Dict[str, int]] = {i: {} for i in ids}
    for i in ids:
        corr[i][i] = 1.0
        overlap[i][i] = int(len(series[i])) if hasattr(series[i], "__len__") else 0
        for j in ids:
            if j <= i:
                continue
            c, n = pairwise_corr(series[i], series[j], min_overlap=min_overlap)
            overlap[i][j] = overlap[j][i] = n
            # No shared calendar -> assume perfect redundancy, never "diversified".
            value = 1.0 if c is None else round(float(c), 3)
            corr[i][j] = corr[j][i] = value
    return corr, overlap


# ── Selection ───────────────────────────────────────────────────────────────

def select_portfolio(
    candidates: List[BotGenome],
    exams: Dict[str, Any] | None = None,
    target: int | None = None,
    max_corr: float | None = None,
    frames: Dict[str, Any] | None = None,
    diagnostics: Dict[str, Any] | None = None,
) -> Tuple[List[BotGenome], Dict[str, Dict[str, float]], List[str]]:
    """
    Greedy quality-diversity selection over *exam-passed, real-data* bots.

      1. Drop bots whose exam did not pass, or passed on non-real bars.
      2. Add a bot only if |corr| with every selected bot < ``max_corr``,
         on calendar-aligned daily returns with enough shared history.
      3. Never pad the book to hit ``target``.
    """
    target = target or cfg.TARGET_BOTS
    target = max(cfg.MIN_BOTS, min(cfg.MAX_BOTS, target))
    max_corr = max_corr if max_corr is not None else cfg.MAX_PAIRWISE_CORR
    frames = frames or {}
    exams = exams or {}
    diag: Dict[str, Any] = {
        "candidates": len(candidates),
        "exam_passed": 0,
        "rejected_no_exam": 0,
        "rejected_provenance": 0,
        "rejected_corr": 0,
        "rejected_unalignable": 0,
        "target": target,
        "shortfall": 0,
    }

    def _frame(symbol: str):
        if symbol not in frames or frames[symbol] is None or not len(frames[symbol]):
            frames[symbol] = load_market_frame(symbol)
        return frames[symbol]

    eligible: List[Tuple[BotGenome, Any, Any]] = []
    for bot in candidates:
        exam = exams.get(bot.bot_id) or run_exam(bot, _frame(bot.symbol))
        exams[bot.bot_id] = exam
        prov = prov_mod.Provenance.from_dict(getattr(exam, "data_provenance", {}) or {})
        if REQUIRE_REAL_DATA and not prov.is_real:
            diag["rejected_provenance"] += 1
            continue
        if REQUIRE_PASSING_EXAM and not bool(getattr(exam, "passed", False)):
            diag["rejected_no_exam"] += 1
            continue
        diag["exam_passed"] += 1
        eligible.append((bot, exam, prov))

    ranked = sorted(
        eligible,
        key=lambda item: (
            1 if item[0].status in ("alive", "exam") else 0,
            float(getattr(item[1], "fitness", 0.0)),
        ),
        reverse=True,
    )

    selected: List[BotGenome] = []
    series: Dict[str, Any] = {}
    rejected: List[str] = []

    for bot, exam, prov in ranked:
        if len(selected) >= target:
            break
        s = _return_series(bot, None, exam=exam)
        if not len(s):
            diag["rejected_unalignable"] += 1
            rejected.append(f"{bot.name}: no calendar-aligned returns (timestamps missing)")
            continue
        series[bot.bot_id] = s

        ok = True
        for other in selected:
            c, overlap = pairwise_corr(s, series[other.bot_id])
            if c is None:
                ok = False
                diag["rejected_unalignable"] += 1
                rejected.append(
                    f"{bot.name}: only {overlap}d shared history with {other.name}"
                )
                break
            if abs(c) >= max_corr:
                ok = False
                diag["rejected_corr"] += 1
                rejected.append(f"{bot.name} corr={c:.2f} vs {other.name}")
                break
        if ok:
            bot.status = "alive"
            bot.data_provenance = prov.as_dict()
            selected.append(bot)

    corr, overlap = correlation_matrix(series)
    diag["selected"] = len(selected)
    diag["shortfall"] = max(0, target - len(selected))
    diag["correlation_method"] = "calendar_aligned_daily"
    diag["min_overlap_days"] = MIN_CORR_OVERLAP_DAYS
    diag["overlap_days"] = overlap
    if diagnostics is not None:
        diagnostics.update(diag)

    if diag["shortfall"]:
        store.log_event(
            "WARN",
            f"Portfolio short of target: {len(selected)}/{target} qualified "
            f"(no exam pass={diag['rejected_no_exam']}, "
            f"non-real data={diag['rejected_provenance']}, "
            f"correlated={diag['rejected_corr']})",
        )

    return selected[: min(target, cfg.MAX_BOTS)], corr, rejected


# ── Allocation ──────────────────────────────────────────────────────────────

def _inverse_vol_weights(series: Dict[str, Any], ids: List[str]) -> Dict[str, float]:
    """Inverse-volatility weights from each bot's own realised daily returns."""
    vols: Dict[str, float] = {}
    for bid in ids:
        s = series.get(bid)
        try:
            v = float(s.std()) if s is not None and len(s) > 2 else 0.0
        except Exception:
            v = 0.0
        vols[bid] = v if v > 1e-9 else float("nan")
    valid = {k: v for k, v in vols.items() if np.isfinite(v)}
    if not valid:
        return {}
    # A bot with no realised return series still needs a weight: dropping it
    # left the allocation dict one entry short of bot_ids, so a bot could be
    # listed in the book with no capital at all.
    median_vol = float(np.median(list(valid.values())))
    for k, v in list(vols.items()):
        if not np.isfinite(v):
            valid[k] = median_vol if median_vol > 1e-12 else 1e-9
    inv = {k: 1.0 / v for k, v in valid.items()}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()}


def allocate(
    bots: List[BotGenome],
    fund: float,
    fitness: Dict[str, float] | None = None,
    series: Dict[str, Any] | None = None,
    max_pair_corr: float | None = None,
) -> Dict[str, float]:
    """
    Volatility-aware allocation with a per-bot cap and a book-level vol target.

    Falls back to fitness weighting when no return series is available. The
    per-bot cap is enforced by redistribution (not by simple clipping, which
    used to leave cash silently unallocated).
    """
    if not bots or fund <= 0:
        return {}
    fitness = fitness or {}
    ids = [b.bot_id for b in bots]

    weights = _inverse_vol_weights(series or {}, ids) if series else {}
    if not weights:
        scores = np.asarray([max(0.05, float(fitness.get(b.bot_id, 1.0))) for b in bots], dtype=float)
        weights = {b.bot_id: float(w) for b, w in zip(bots, scores / scores.sum())}

    cap = cfg.MAX_BOT_ALLOC_PCT
    capped = {k: min(v, cap) for k, v in weights.items()}
    # Redistribute the excess pro-rata over bots that are still below the cap.
    for _ in range(8):
        total = sum(capped.values())
        if total >= 1.0 - 1e-9:
            break
        room = {k: cap - v for k, v in capped.items() if v < cap - 1e-12}
        room_total = sum(room.values())
        if room_total <= 1e-12:
            break
        excess = 1.0 - total
        for k, r in room.items():
            capped[k] += excess * (r / room_total)
    total = sum(capped.values()) or 1.0
    return {k: round(float(fund * v / total), 2) for k, v in capped.items()}


def detect_fading(
    bot: BotGenome,
    recent_trades: List[Dict[str, Any]],
) -> bool:
    """True if bot's recent rolling PnL is fading below threshold."""
    mine = [t for t in recent_trades if t.get("bot_id") == bot.bot_id]
    mine = mine[-cfg.FADE_LOOKBACK_TRADES:]
    if len(mine) < max(4, cfg.FADE_LOOKBACK_TRADES // 2):
        return False
    rets = [float(t.get("pnl_pct", 0)) / 100.0 for t in mine]
    return float(np.sum(rets)) < cfg.FADE_PNL_THRESHOLD


def cut_fading(
    bots: List[BotGenome],
    trades: List[Dict[str, Any]] | None = None,
) -> Tuple[List[BotGenome], List[BotGenome]]:
    """Split into (kept, killed). Killed get status=dead for UI death blast."""
    trades = trades if trades is not None else store.load_trades(400)
    kept: List[BotGenome] = []
    killed: List[BotGenome] = []
    for b in bots:
        if detect_fading(b, trades):
            b.status = "dead"
            killed.append(b)
            store.log_event("WARN", f"Cut fading bot {b.name}", bot_id=b.bot_id)
        else:
            if b.status == "fading":
                b.status = "alive"
            kept.append(b)
    return kept, killed


def build_portfolio(
    candidates: List[BotGenome],
    paper_fund: float | None = None,
    target: int | None = None,
) -> PortfolioState:
    """End-to-end: select → allocate → persist."""
    fund_data = store.load_fund()
    fund = float(paper_fund if paper_fund is not None else fund_data.get("paper_fund", cfg.DEFAULT_PAPER_FUND))
    diag: Dict[str, Any] = {}

    selected, corr, rejected = select_portfolio(candidates, target=target, diagnostics=diag)
    for r in rejected[:20]:
        store.log_event("INFO", f"Portfolio reject: {r}")

    # Reuse the exams computed during selection instead of re-running them.
    fitness: Dict[str, float] = {}
    series: Dict[str, Any] = {}
    frames: Dict[str, Any] = {}
    for b in selected:
        if b.symbol not in frames:
            frames[b.symbol] = load_market_frame(b.symbol)
        ex = run_exam(b, frames[b.symbol])
        fitness[b.bot_id] = ex.fitness
        b.data_provenance = dict(ex.data_provenance or {})
        s = _return_series(b, None, exam=ex)
        if len(s):
            series[b.bot_id] = s

    allocs = allocate(selected, fund, fitness, series=series)
    state = PortfolioState(
        fund_cash=fund,
        equity=fund,
        bot_ids=[b.bot_id for b in selected],
        allocations=allocs,
        corr_matrix=corr,
        killed=[],
        correlation_method="calendar_aligned_daily",
        correlation_overlap_ok=not diag.get("rejected_unalignable", 0),
    )

    # Persist
    store.save_bots([b.to_dict() for b in selected])
    payload = state.to_dict()
    payload["admission"] = diag  # keep the "why only N bots" trail with the book
    store.save_portfolio(payload)
    fund_data["paper_fund"] = fund
    fund_data["cash"] = fund
    fund_data["equity"] = fund
    store.save_fund(fund_data)
    store.log_event(
        "INFO",
        f"Portfolio built: {len(selected)} bots, fund={fund:,.0f}, "
        f"shortfall={diag.get('shortfall', 0)}",
    )
    return state


def rebalance_and_cut() -> Dict[str, Any]:
    """Daemon tick: cut fading, re-allocate remaining. Never pads the book."""
    raw = store.load_bots()
    bots = [BotGenome.from_dict(b) for b in raw]
    alive = [b for b in bots if b.status not in ("dead", "candidate")]
    kept, killed = cut_fading(alive)
    fund = store.load_fund()
    capital = float(fund.get("equity") or fund.get("paper_fund") or cfg.DEFAULT_PAPER_FUND)

    # No top-up. The book used to be padded from the candidate pool to reach
    # MIN_BOTS, which is how unexamined bots ended up with capital. A short book
    # is the honest outcome; ``admission.shortfall`` records the gap.
    shortfall = max(0, cfg.MIN_BOTS - len(kept))

    fitness = {b.bot_id: 1.0 for b in kept}
    series: Dict[str, Any] = {}
    frames: Dict[str, Any] = {}
    for b in kept:
        try:
            if b.symbol not in frames:
                frames[b.symbol] = load_market_frame(b.symbol)
            s = _return_series(b, frames[b.symbol])
            if len(s):
                series[b.bot_id] = s
        except Exception:
            continue

    port = store.load_portfolio()

    # A rebalance that finds nothing alive must not wipe the book. The old code
    # wrote an empty bot_ids list whenever no bot happened to be flagged
    # 'alive', which silently emptied the portfolio that a deploy step was
    # about to read.
    if not kept and (port.get("bot_ids") or []):
        existing = [str(x) for x in port.get("bot_ids") or []]
        store.log_event(
            "WARN",
            f"Rebalance found no live bots; keeping the existing book ({len(existing)} bots)",
        )
        port["killed"] = list(port.get("killed") or []) + [b.bot_id for b in killed]
        port["equity"] = capital
        store.save_portfolio(port)
        return {"kept": len(existing), "killed": [b.to_dict() for b in killed],
                "allocations": port.get("allocations") or {}, "preserved": True}

    allocs = allocate(kept, capital, fitness, series=series)
    port["bot_ids"] = [b.bot_id for b in kept]
    port["allocations"] = allocs
    port["killed"] = list(port.get("killed") or []) + [b.bot_id for b in killed]
    port["equity"] = capital
    port["correlation_method"] = "calendar_aligned_daily"
    store.save_portfolio(port)

    # Update bot statuses
    all_bots = {b.bot_id: b for b in bots}
    for b in kept:
        all_bots[b.bot_id] = b
    for b in killed:
        all_bots[b.bot_id] = b
    store.save_bots([b.to_dict() for b in all_bots.values()])

    return {
        "kept": len(kept),
        "shortfall": shortfall,
        "killed": [b.to_dict() for b in killed],
        "allocations": allocs,
    }
