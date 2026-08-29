"""
Situational quant interceptor — VIX / latency kill switch.

``SituationalQuantTackle.inspect_market_vitals`` is the pre-deployment
interceptor. ``evaluate_live_vitals`` resolves live India VIX + price-server
latency (10s cache) and is called from the Aegis tab, AITE ``deploy_bots`` /
``swap_bot``, the quant orchestrator paper-execution step, the agent-debate
graph, HOLD banners, ``MT5PaperBroker.execute``, and ``execution_monitor_edge``.
``flatten_paper_on_vix_emergency`` closes paper books on VIX > 28 only.

It does **not** replace existing safety layers:

* Broker two-layer arming (``ZERO_BROKER_ARMED`` + ``BrokerAdapter(armed=…)``)
* Monte Carlo ruin gate in ``engine.quant_orchestrator``
  (``probability_of_ruin < MC_MAX_RUIN_PROBABILITY``)
* Risk-manager VIX bands in ``engine.multi_agent_consensus``

Those layers stay independently enforced. This module only answers: should
autonomous routing proceed given India VIX and local price-server latency?
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Mapping, Optional

# ── Hardcoded containment limits (spec) ──────────────────────────────────────
VIX_MAX_EMERGENCY_LIMIT: float = 28.0
LATENCY_MAX_LIMIT_MS: float = 500.0

# UI / fallback path when live vitals cannot be resolved
DEFAULT_INDIA_VIX: float = 16.5
DEFAULT_SERVER_LATENCY_MS: float = 45.0

_LATENCY_PROBE_TTL_S: float = 10.0
_GATE_TTL_S: float = 10.0
_latency_cache: tuple[float, str, float] | None = None  # value, source, monotonic ts
_gate_cache: tuple[float, dict[str, Any]] | None = None


def _coerce_vital(value: Any, *, name: str) -> tuple[Optional[float], Optional[str]]:
    """Return (finite non-negative float, None) or (None, reason)."""
    if value is None:
        return None, f"{name} is missing (None)"
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None, f"{name} is not numeric ({value!r})"
    if math.isnan(parsed) or math.isinf(parsed):
        return None, f"{name} is not a finite number ({value!r})"
    if parsed < 0:
        return None, f"{name} is negative ({parsed:g})"
    return parsed, None


def _unreadable_hold(reason: str) -> tuple[bool, str]:
    return True, (
        f"[SITUATIONAL TACKLE ACTIVATED] VITALS UNREADABLE: {reason}. "
        "Bypassing autonomous LLM agent frameworks. "
        "Bypassing active portfolio deployment lanes. Status set to HOLD."
    )


def existing_broker_interlock() -> dict[str, Any]:
    """Read-only snapshot of the existing broker safety gate. Never mutates env."""
    raw = os.environ.get("ZERO_BROKER_ARMED", "unset")
    armed = raw == "1"
    return {
        "armed": armed,
        "env_value": raw,
        "label": "ARMED" if armed else "DISARMED",
        "note": (
            "Broker two-layer interlock (ZERO_BROKER_ARMED + adapter armed flag) "
            "remains independently enforced. Situational tackle does not arm or disarm it."
        ),
    }


def resolve_india_vix(matrix: Optional[Mapping[str, Any]] = None) -> tuple[float, str]:
    """Best available India VIX plus a short source label.

    Order: live ``fetch_india_vix`` (cached) → prediction-matrix ``india_vix``
    → default 16.5. Never raises.
    """
    try:
        from data.india_vix import fetch_india_vix

        live = fetch_india_vix()
        parsed, err = _coerce_vital(live, name="India VIX")
        if parsed is not None and parsed > 0:
            return parsed, "live:data.india_vix"
    except Exception:
        pass

    if isinstance(matrix, Mapping):
        for key in ("NIFTY 50", "BANKNIFTY", "SENSEX"):
            row = matrix.get(key) or {}
            if not isinstance(row, Mapping):
                continue
            parsed, err = _coerce_vital(row.get("india_vix"), name="India VIX")
            if parsed is not None and parsed > 0:
                return parsed, f"matrix:{key}.india_vix"
        for key in ("NIFTY 50", "BANKNIFTY", "SENSEX"):
            row = matrix.get(key) or {}
            if not isinstance(row, Mapping):
                continue
            parsed, err = _coerce_vital(row.get("vix"), name="VIX")
            if parsed is not None and parsed > 0:
                return parsed, f"matrix:{key}.vix (US/proxy)"

    return DEFAULT_INDIA_VIX, "default"


def probe_server_latency(timeout_s: float = 0.4) -> tuple[float, str]:
    """Round-trip ms to the local ZERO price-server ``/health``.

    Falls back to 45.0 when the server is unreachable. Cached briefly so
    Streamlit reruns do not hammer localhost. Never raises.
    """
    global _latency_cache
    now = time.monotonic()
    if _latency_cache is not None:
        value, source, ts = _latency_cache
        if (now - ts) < _LATENCY_PROBE_TTL_S:
            return value, source

    try:
        import requests
        from engine.live_price_server import PRICE_SERVER_PORT

        url = f"http://127.0.0.1:{PRICE_SERVER_PORT}/health"
        t0 = time.perf_counter()
        resp = requests.get(url, timeout=timeout_s)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if resp.ok:
            parsed, err = _coerce_vital(elapsed_ms, name="latency")
            if parsed is not None:
                _latency_cache = (parsed, "live:price_server/health", now)
                return parsed, "live:price_server/health"
    except Exception:
        pass

    _latency_cache = (DEFAULT_SERVER_LATENCY_MS, "default", now)
    return DEFAULT_SERVER_LATENCY_MS, "default"


class SituationalQuantTackle:
    """Hardcoded VIX / latency interceptor. Call before autonomous deployment."""

    VIX_MAX_EMERGENCY_LIMIT = VIX_MAX_EMERGENCY_LIMIT
    LATENCY_MAX_LIMIT_MS = LATENCY_MAX_LIMIT_MS

    @staticmethod
    def inspect_market_vitals(
        india_vix: float,
        local_server_latency: float,
    ) -> tuple[bool, str]:
        """Evaluate live market vital metrics to protect system equity.

        Returns
        -------
        (tripped, message)
            ``True`` means override / HOLD — do not route through agent graphs.
        """
        vix, vix_err = _coerce_vital(india_vix, name="India VIX")
        if vix_err:
            return _unreadable_hold(vix_err)
        if vix == 0:
            return _unreadable_hold("India VIX is zero (unreadable vital)")

        lat, lat_err = _coerce_vital(local_server_latency, name="local server latency")
        if lat_err:
            return _unreadable_hold(lat_err)

        if vix > VIX_MAX_EMERGENCY_LIMIT:
            override_action = (
                f"[SITUATIONAL TACKLE ACTIVATED] EMERGENCY FATAL OVERRIDE: India VIX spike "
                f"detected at {vix:g}. Bypassing autonomous LLM agent frameworks. "
                f"Executing hardcoded risk protocol: Purging all orders and closing current exposure to 0."
            )
            return True, override_action

        if lat > LATENCY_MAX_LIMIT_MS:
            override_action = (
                f"[SITUATIONAL TACKLE ACTIVATED] API INFRASTRUCTURE WARNING: Price server latency "
                f"is at {lat:g}ms. Risk of execution slippage is extreme. "
                f"Bypassing active portfolio deployment lanes. Status set to HOLD."
            )
            return True, override_action

        return (
            False,
            "System vitals baseline cleared. Execution routing authorized through standard agent debate graphs.",
        )

    @staticmethod
    def resolve_india_vix(matrix: Optional[Mapping[str, Any]] = None) -> tuple[float, str]:
        return resolve_india_vix(matrix)

    @staticmethod
    def probe_server_latency(timeout_s: float = 0.4) -> tuple[float, str]:
        return probe_server_latency(timeout_s)

    @staticmethod
    def existing_broker_interlock() -> dict[str, Any]:
        return existing_broker_interlock()

    @staticmethod
    def evaluate_live_vitals(
        matrix: Optional[Mapping[str, Any]] = None,
        *,
        india_vix: Any = None,
        local_server_latency: Any = None,
    ) -> dict[str, Any]:
        return evaluate_live_vitals(
            matrix,
            india_vix=india_vix,
            local_server_latency=local_server_latency,
        )


def evaluate_live_vitals(
    matrix: Optional[Mapping[str, Any]] = None,
    *,
    india_vix: Any = None,
    local_server_latency: Any = None,
) -> dict[str, Any]:
    """Resolve live VIX + latency (unless passed) and inspect market vitals.

    Used by the Aegis UI, AITE deploy, quant orchestrator, and agent debate.
    Results from fully-resolved (no override) calls are cached briefly so a
    three-index debate + deploy in the same second share one probe.
    Never raises.
    """
    global _gate_cache
    use_cache = india_vix is None and local_server_latency is None
    now = time.monotonic()
    if use_cache and _gate_cache is not None:
        ts, cached = _gate_cache
        if (now - ts) < _GATE_TTL_S:
            return dict(cached)

    vix_source = "caller"
    lat_source = "caller"
    probe_failed = False
    try:
        if india_vix is None:
            india_vix, vix_source = resolve_india_vix(matrix)
        if local_server_latency is None:
            local_server_latency, lat_source = probe_server_latency()
        tripped, message = SituationalQuantTackle.inspect_market_vitals(
            india_vix, local_server_latency
        )
    except Exception as exc:
        probe_failed = True
        tripped, message = _unreadable_hold(str(exc))
        if india_vix is None:
            india_vix = DEFAULT_INDIA_VIX
            vix_source = "unreadable"
        if local_server_latency is None:
            local_server_latency = DEFAULT_SERVER_LATENCY_MS
            lat_source = "unreadable"

    vix_val, vix_err = _coerce_vital(india_vix, name="India VIX")
    lat_val, lat_err = _coerce_vital(local_server_latency, name="latency")
    vix_emergency = bool(
        not probe_failed
        and vix_val is not None
        and vix_val > VIX_MAX_EMERGENCY_LIMIT
    )
    if probe_failed or vix_err or lat_err or (vix_val is not None and vix_val == 0):
        trip_reason = "unreadable"
        vix_emergency = False
    elif vix_emergency:
        trip_reason = "vix_emergency"
    elif lat_val is not None and lat_val > LATENCY_MAX_LIMIT_MS:
        trip_reason = "latency_hold"
    else:
        trip_reason = "cleared"

    payload: dict[str, Any] = {
        "tripped": bool(tripped),
        "authorized": not bool(tripped),
        "message": message,
        "india_vix": india_vix,
        "vix_source": vix_source,
        "latency_ms": local_server_latency,
        "latency_source": lat_source,
        "vix_emergency": vix_emergency,
        "trip_reason": trip_reason,
    }
    if use_cache:
        _gate_cache = (now, dict(payload))
    return payload


def is_vix_emergency(vitals: Optional[Mapping[str, Any]] = None) -> bool:
    """True only when India VIX exceeds the emergency limit.

    Latency HOLD and unreadable vitals are not a VIX emergency — those
    block new fills but must not flatten paper books.
    """
    if not isinstance(vitals, Mapping):
        return False
    if "vix_emergency" in vitals:
        return bool(vitals.get("vix_emergency"))
    if vitals.get("trip_reason") == "vix_emergency":
        return True
    parsed, err = _coerce_vital(vitals.get("india_vix"), name="India VIX")
    if err or parsed is None:
        return False
    return parsed > VIX_MAX_EMERGENCY_LIMIT


def fills_blocked_by_vitals(
    matrix: Optional[Mapping[str, Any]] = None,
) -> tuple[bool, dict[str, Any]]:
    """Fail-closed fill gate for already-deployed paper / AITE execution.

    Returns ``(blocked, vitals)``. Any unreadable payload is treated as
    tripped so new fills cannot proceed.
    """
    try:
        vitals = evaluate_live_vitals(matrix)
        if not isinstance(vitals, dict) or "tripped" not in vitals:
            raise TypeError("vitals payload missing tripped")
        return bool(vitals.get("tripped")), vitals
    except Exception as exc:
        tripped, message = _unreadable_hold(str(exc))
        return True, {
            "tripped": True,
            "authorized": False,
            "message": message,
            "india_vix": None,
            "latency_ms": None,
            "vix_emergency": False,
            "trip_reason": "unreadable",
            "unreadable": True,
        }


def flatten_paper_on_vix_emergency(
    vitals: Optional[Mapping[str, Any]] = None,
    *,
    brokerage: Any = None,
) -> dict[str, Any]:
    """Close paper exposure when VIX > 28. Latency HOLD does not flatten.

    Uses ``PaperBrokerage.execute_order`` SELL and ``PaperFund.apply_fill``
    SELL at average cost. Idempotent: empty books are a no-op. Never
    imports or calls live Dhan / Fyers / Kite / Angel adapters.
    """
    if vitals is None:
        try:
            vitals = evaluate_live_vitals()
        except Exception as exc:
            return {
                "ok": False,
                "flattened": False,
                "reason": "vitals_unreadable",
                "error": str(exc),
                "live_brokers_touched": False,
            }

    if not is_vix_emergency(vitals):
        return {
            "ok": True,
            "flattened": False,
            "reason": str((vitals or {}).get("trip_reason") or "not_vix_emergency"),
            "live_brokers_touched": False,
        }

    fills: list[dict[str, Any]] = []
    errors: list[str] = []

    try:
        from engine.paper_brokerage import PaperBrokerage

        book = brokerage if brokerage is not None else PaperBrokerage.load()
        for symbol, qty in list(getattr(book, "positions", {}) or {}).items():
            qty_f = float(qty or 0.0)
            if qty_f <= 0:
                continue
            px = float((getattr(book, "avg_cost", {}) or {}).get(symbol) or 0.0)
            if px <= 0:
                errors.append(f"paper_brokerage:{symbol}:no_price")
                continue
            report = book.execute_order(str(symbol), "SELL", qty_f, px)
            fills.append({"book": "paper_brokerage", **(report or {})})
        if any(f.get("status") == "filled" for f in fills) and hasattr(book, "save_log"):
            book.save_log()
    except Exception as exc:
        errors.append(f"paper_brokerage:{exc}")

    try:
        from engine.aite.paper_fund import get_paper_fund

        fund = get_paper_fund()
        for symbol, pos in list(getattr(fund, "positions", {}) or {}).items():
            if not isinstance(pos, Mapping):
                continue
            qty_f = float(pos.get("qty") or 0.0)
            if qty_f <= 0:
                continue
            px = float(pos.get("avg_cost") or 0.0)
            if px <= 0:
                errors.append(f"paper_fund:{symbol}:no_price")
                continue
            report = fund.apply_fill(
                str(symbol),
                "SELL",
                qty_f,
                px,
                bot_id=str(pos.get("bot_id") or ""),
            )
            fills.append({"book": "paper_fund", **(report or {})})
    except Exception as exc:
        errors.append(f"paper_fund:{exc}")

    try:
        from engine.aite import store

        store.log_event(
            "WARN",
            (
                f"VIX emergency paper flatten fills={len(fills)} "
                f"errors={len(errors)} vix={vitals.get('india_vix')}"
            ),
            action="paper_flatten_vix_emergency",
            vix=vitals.get("india_vix"),
            fills=len(fills),
            errors=list(errors),
            live_brokers_touched=False,
        )
    except Exception:
        pass

    closed = [
        f for f in fills
        if f.get("status") == "filled" or f.get("ok") is True
    ]
    return {
        "ok": not errors,
        "flattened": bool(closed),
        "reason": "vix_emergency" if closed else "already_flat",
        "fills": fills,
        "errors": errors,
        "live_brokers_touched": False,
    }


if __name__ == "__main__":
    ok, msg = SituationalQuantTackle.inspect_market_vitals(16.5, 45.0)
    print("cleared", ok, msg)
    ok, msg = SituationalQuantTackle.inspect_market_vitals(29.0, 45.0)
    print("vix trip", ok, msg)
    ok, msg = SituationalQuantTackle.inspect_market_vitals(16.5, 600.0)
    print("lat trip", ok, msg)
    ok, msg = SituationalQuantTackle.inspect_market_vitals(None, 45.0)  # type: ignore[arg-type]
    print("none", ok, msg)
