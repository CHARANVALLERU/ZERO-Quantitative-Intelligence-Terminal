"""
engine.broker.order_state — one order, one explicit lifecycle.
============================================================

``backend/main.py`` created an ``Order`` row with ``status="PENDING"`` and
queued it; nothing defined what may follow, so a rejected order could be
"filled", a cancelled one re-sent, and a retried submission could double the
position. This module is the missing definition:

* a closed set of states and legal transitions (everything else raises);
* idempotency: re-submitting the same client key returns the existing order
  instead of creating a second one;
* every transition appended to ``db/broker/order_events.jsonl`` with the actor,
  reason and broker payload, so the broker's truth can be reconciled against
  ours;
* ``reconcile()`` compares our state with a broker snapshot and reports drift
  (missing fills, ghost orders, quantity mismatches).

Pure standard library, no broker SDK, no network.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set

ORDER_DIR = os.getenv("ZERO_ORDER_DIR", os.path.join("db", "broker"))
EVENTS_PATH = os.path.join(ORDER_DIR, "order_events.jsonl")
ORDERS_PATH = os.path.join(ORDER_DIR, "orders.json")

# ── States ──────────────────────────────────────────────────────────────────

PENDING_NEW = "PENDING_NEW"
VALIDATED = "VALIDATED"
SENT = "SENT"
ACKED = "ACKED"
PARTIAL = "PARTIAL"
FILLED = "FILLED"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"

TERMINAL: Set[str] = {FILLED, CANCELLED, REJECTED, EXPIRED}

TRANSITIONS: Dict[str, Set[str]] = {
    PENDING_NEW: {VALIDATED, REJECTED, CANCELLED},
    VALIDATED: {SENT, REJECTED, CANCELLED, EXPIRED},
    SENT: {ACKED, REJECTED, CANCELLED, EXPIRED},
    ACKED: {PARTIAL, FILLED, CANCELLED, REJECTED, EXPIRED},
    PARTIAL: {PARTIAL, FILLED, CANCELLED, EXPIRED},
    FILLED: set(),
    CANCELLED: set(),
    REJECTED: set(),
    EXPIRED: set(),
}

_lock = threading.Lock()
_orders: Dict[str, Dict[str, Any]] = {}
_by_client_key: Dict[str, str] = {}
_loaded = False


@dataclass
class Order:
    order_id: str
    symbol: str
    side: str                     # BUY | SELL
    quantity: float
    order_type: str = "MARKET"
    limit_price: Optional[float] = None
    filled_quantity: float = 0.0
    average_price: Optional[float] = None
    state: str = PENDING_NEW
    client_key: str = ""
    broker_order_id: str = ""
    reject_reason: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class IllegalTransition(RuntimeError):
    """Raised when a state change would corrupt the order's history."""


def _ensure_loaded() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(ORDERS_PATH, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        for row in doc.get("orders") or []:
            order = Order(**{k: v for k, v in row.items()
                             if k in Order.__dataclass_fields__})
            _orders[order.order_id] = order.to_dict()
            if order.client_key:
                _by_client_key[order.client_key] = order.order_id
    except Exception:
        pass


def _persist() -> None:
    try:
        os.makedirs(ORDER_DIR, exist_ok=True)
        tmp = ORDERS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"orders": list(_orders.values()),
                       "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, fh, indent=2)
        os.replace(tmp, ORDERS_PATH)
    except Exception:
        pass


def _event(order: Dict[str, Any], from_state: str, to_state: str,
           actor: str, reason: str, payload: Optional[Dict[str, Any]]) -> None:
    row = {
        "ts": time.time(),
        "order_id": order["order_id"],
        "client_key": order.get("client_key", ""),
        "from": from_state,
        "to": to_state,
        "actor": actor,
        "reason": reason,
        "payload": payload or {},
    }
    order.setdefault("events", []).append(row)
    try:
        os.makedirs(ORDER_DIR, exist_ok=True)
        with open(EVENTS_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str, sort_keys=True) + "\n")
    except Exception:
        pass


def create(symbol: str, side: str, quantity: float, order_type: str = "MARKET",
           limit_price: Optional[float] = None, client_key: str = "",
           actor: str = "api") -> Dict[str, Any]:
    """
    Create (or return) an order.

    Supplying the same ``client_key`` twice returns the first order untouched:
    that is what makes a retried HTTP request safe.
    """
    with _lock:
        _ensure_loaded()
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"invalid side: {side!r}")
        if float(quantity) <= 0:
            raise ValueError("quantity must be positive")
        if client_key and client_key in _by_client_key:
            return dict(_orders[_by_client_key[client_key]])

        order = Order(
            order_id=f"ORD-{uuid.uuid4().hex[:12]}",
            symbol=str(symbol).upper(),
            side=side,
            quantity=float(quantity),
            order_type=order_type.upper(),
            limit_price=float(limit_price) if limit_price is not None else None,
            client_key=client_key or f"auto-{uuid.uuid4().hex[:8]}",
        )
        if order.order_type == "LIMIT" and order.limit_price is None:
            order.state = REJECTED
            order.reject_reason = "LIMIT order without a limit price"
        d = order.to_dict()
        _orders[d["order_id"]] = d
        _by_client_key[d["client_key"]] = d["order_id"]
        _event(d, "", d["state"], actor, "created", None)
        _persist()
        return dict(d)


def transition(order_id: str, to_state: str, actor: str = "system",
               reason: str = "", payload: Optional[Dict[str, Any]] = None,
               filled_quantity: Optional[float] = None,
               average_price: Optional[float] = None) -> Dict[str, Any]:
    """Move an order to ``to_state`` if (and only if) that transition is legal."""
    with _lock:
        _ensure_loaded()
        order = _orders.get(order_id)
        if order is None:
            raise KeyError(f"unknown order {order_id}")
        current = order["state"]
        if to_state not in TRANSITIONS.get(current, set()):
            if current == to_state and to_state == PARTIAL:
                pass  # repeated partial fills are legal
            else:
                raise IllegalTransition(f"{current} -> {to_state} is not allowed")

        order["state"] = to_state
        order["updated_at"] = time.time()
        if filled_quantity is not None:
            order["filled_quantity"] = float(filled_quantity)
        if average_price is not None:
            order["average_price"] = float(average_price)
        if to_state == REJECTED and reason:
            order["reject_reason"] = reason
        _event(order, current, to_state, actor, reason, payload)
        _persist()
        return dict(order)


def get(order_id: str) -> Optional[Dict[str, Any]]:
    _ensure_loaded()
    return dict(_orders[order_id]) if order_id in _orders else None


def by_client_key(client_key: str) -> Optional[Dict[str, Any]]:
    _ensure_loaded()
    oid = _by_client_key.get(client_key)
    return dict(_orders[oid]) if oid else None


def open_orders() -> List[Dict[str, Any]]:
    _ensure_loaded()
    return [dict(o) for o in _orders.values() if o["state"] not in TERMINAL]


def reconcile(broker_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Compare our book with the broker's snapshot.

    ``broker_orders`` rows need ``order_id`` (or ``broker_order_id``), ``state``
    and optionally ``filled_quantity``. Reports ghost/unknown orders, state
    drift and quantity mismatches — the three ways a paper book goes wrong.
    """
    _ensure_loaded()
    broker = {str(o.get("order_id") or o.get("broker_order_id") or ""): o
              for o in broker_orders or []}
    unknown, drift, qty_mismatch = [], [], []
    for oid, order in _orders.items():
        b = broker.get(oid) or broker.get(str(order.get("broker_order_id") or ""))
        if b is None:
            if order["state"] not in TERMINAL:
                unknown.append(oid)
            continue
        b_state = str(b.get("state") or "").upper()
        if b_state and b_state != order["state"]:
            drift.append({"order_id": oid, "ours": order["state"], "broker": b_state})
        if b.get("filled_quantity") is not None:
            if abs(float(b["filled_quantity"]) - float(order["filled_quantity"])) > 1e-6:
                qty_mismatch.append({
                    "order_id": oid, "ours": order["filled_quantity"],
                    "broker": float(b["filled_quantity"]),
                })
    ghosts = [oid for oid in broker if oid and oid not in _orders]
    return {
        "checked": len(_orders),
        "broker_orders": len(broker),
        "missing_at_broker": unknown,
        "state_drift": drift,
        "quantity_mismatch": qty_mismatch,
        "ghost_orders": ghosts,
        "clean": not (unknown or drift or qty_mismatch or ghosts),
    }


if __name__ == "__main__":
    import json as _json
    o = create("NIFTY", "BUY", 75, client_key="demo-1")
    transition(o["order_id"], VALIDATED, actor="risk", reason="within limits")
    transition(o["order_id"], SENT, actor="gateway", reason="submitted")
    print(_json.dumps(get(o["order_id"]), indent=2, default=str))
