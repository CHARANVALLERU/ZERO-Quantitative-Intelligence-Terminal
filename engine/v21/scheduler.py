"""
engine.v21.scheduler — the timer behind the "24/7" claim.
=========================================================

`ecosystem.run_cycle` is one pass of the six-stage loop. This module owns the
clock: a single daemon thread per process runs that pass on a fixed interval over
a configurable index list, so the claim is a running job rather than a button.

A loop meant to run for weeks has to stay small and stay alive:

* **Bounded on disk.** Status is one fixed-key JSON object
  (``db/v21/scheduler.json``); the run log is size-rotated
  (``db/v21/scheduler.log`` + ``.1/.2/.3``). Nothing here appends without a cap.
* **One pass at a time.** The loop runs cycles sequentially; a slow cycle delays
  the next tick instead of stacking threads.
* **Never raises.** A failed cycle is logged and counted, then retried with an
  exponential backoff capped at the interval. Only :func:`stop` ends the thread.
* **Testable offline.** :func:`run_once` accepts the cycle function, so tests
  never touch a vendor or the network.

Environment::

    ZERO_V21_SCHEDULER=off              disable the auto-start from the app
    ZERO_V21_CYCLE_SECONDS=900          seconds between passes (min 60)
    ZERO_V21_CYCLE_INDEXES=NIFTY,BANKNIFTY,SENSEX
    ZERO_V21_FIRST_DELAY=20             seconds before the first pass
    ZERO_V21_LOG_MAX_BYTES=262144       rotate the run log at this size

CLI::

    python -m engine.v21.scheduler --once      # one pass, print the record
    python -m engine.v21.scheduler --loop      # foreground 24/7 loop
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Sequence

DEFAULT_INDEXES: Sequence[str] = ("NIFTY", "BANKNIFTY", "SENSEX")
MIN_INTERVAL_SECONDS = 60
DEFAULT_INTERVAL_SECONDS = 900
LOG_BACKUPS = 3
DEFAULT_LOG_MAX_BYTES = 256 * 1024
ERROR_CHARS = 300

_OFF_VALUES = {"0", "off", "false", "no", "disabled"}

_LOCK = threading.RLock()
_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None
_RUNTIME: Dict[str, Any] = {
    "running": False,
    "started_at": None,
    "thread": None,
    "interval": None,
    "indexes": [],
    "next_run": None,
    "backoff": 0,
}


# ── Paths / configuration ────────────────────────────────────────────────────

def _db_dir() -> str:
    return os.path.join("db", "v21")


def state_path() -> str:
    return os.path.join(_db_dir(), "scheduler.json")


def log_path() -> str:
    return os.path.join(_db_dir(), "scheduler.log")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def interval_seconds() -> int:
    return _env_int("ZERO_V21_CYCLE_SECONDS", DEFAULT_INTERVAL_SECONDS, MIN_INTERVAL_SECONDS)


def log_max_bytes() -> int:
    return _env_int("ZERO_V21_LOG_MAX_BYTES", DEFAULT_LOG_MAX_BYTES, 1024)


def enabled() -> bool:
    return os.getenv("ZERO_V21_SCHEDULER", "on").strip().lower() not in _OFF_VALUES


def indexes() -> List[str]:
    """Index list for each tick, canonicalised and de-duplicated."""
    raw = os.getenv("ZERO_V21_CYCLE_INDEXES", ",".join(DEFAULT_INDEXES))
    wanted = [p.strip() for p in raw.split(",") if p.strip()] or list(DEFAULT_INDEXES)
    try:
        from engine.v21 import indices as idx

        canon = [idx.canonical(name) for name in wanted]
    except Exception:
        canon = [name.upper() for name in wanted]
    out: List[str] = []
    for key in canon:
        if key not in out:
            out.append(key)
    return out


# ── Bounded log / state ──────────────────────────────────────────────────────

def _log(line: str) -> None:
    """Append one timestamped line, rotating the file first when it is too big."""
    try:
        os.makedirs(_db_dir(), exist_ok=True)
        path = log_path()
        try:
            too_big = os.path.getsize(path) > log_max_bytes()
        except OSError:
            too_big = False
        if too_big:
            for n in range(LOG_BACKUPS, 0, -1):
                src = path if n == 1 else f"{path}.{n - 1}"
                dst = f"{path}.{n}"
                try:
                    if os.path.exists(src):
                        os.replace(src, dst)
                except OSError:
                    pass
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {line}\n")
    except Exception:
        pass


def _read_state() -> Dict[str, Any]:
    try:
        with open(state_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(record: Dict[str, Any]) -> Dict[str, Any]:
    """merge one run into the fixed-key status object (never grows)."""
    with _LOCK:
        state = _read_state()
        runs = int(state.get("runs") or 0) + 1
        failures = int(state.get("failures") or 0) + (0 if record["ok"] else 1)
        streak = int(state.get("consecutive_failures") or 0)
        state = {
            "version": 1,
            "runs": runs,
            "failures": failures,
            "consecutive_failures": 0 if record["ok"] else streak + 1,
            "last_run_ts": record["ts"],
            "last_run_iso": record["iso"],
            "last_index": record["index"],
            "last_ok": bool(record["ok"]),
            "last_seconds": record["seconds"],
            "last_error": (record.get("error") or "")[:ERROR_CHARS] or None,
            "last_suggestion": (record.get("suggestion") or "")[:200] or None,
            "updated_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            os.makedirs(_db_dir(), exist_ok=True)
            tmp = state_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, default=str)
            os.replace(tmp, state_path())
        except Exception:
            pass
        return state


# ── One pass ─────────────────────────────────────────────────────────────────

def _default_cycle(index: str) -> Dict[str, Any]:
    from engine.v21.ecosystem import run_cycle

    return run_cycle(index)


def run_once(index: Optional[str] = None,
             cycle: Optional[Callable[[str], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Run a single ecosystem pass and record it. Never raises."""
    key = index or (indexes() or ["NIFTY"])[0]
    started = time.time()
    out: Dict[str, Any] = {}
    error: Optional[str] = None
    try:
        out = (cycle or _default_cycle)(key) or {}
        ok = bool(out.get("available", True))
        if not ok:
            error = str(out.get("reason") or "cycle returned unavailable")
    except Exception as exc:  # noqa: BLE001 — the loop must survive anything
        ok = False
        error = f"{type(exc).__name__}: {exc}"
        _log(f"ERROR {key} {error} | {traceback.format_exc(limit=2).splitlines()[-1][:200]}")

    suggestion = out.get("suggestion") if isinstance(out, dict) else None
    record = {
        "ts": round(started, 3),
        "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "index": key,
        "ok": ok,
        "seconds": round(time.time() - started, 1),
        "error": error,
        "suggestion": (suggestion or {}).get("label") if isinstance(suggestion, dict) else None,
    }
    state = _write_state(record)
    _RUNTIME["last_record"] = record
    _RUNTIME["state"] = state
    if ok:
        _log(f"OK   {key} {record['seconds']}s · {record['suggestion'] or 'no suggestion'}")
    elif error:
        _log(f"FAIL {key} {error}")
    return record


# ── The loop ─────────────────────────────────────────────────────────────────

def _loop(keys: Sequence[str], interval: int,
          runner: Optional[Callable[[str], Dict[str, Any]]],
          first_delay: float) -> None:
    if first_delay > 0 and _STOP.wait(first_delay):
        return
    while not _STOP.is_set():
        tick = time.time()
        all_ok = True
        for key in keys:
            if _STOP.is_set():
                break
            rec = run_once(index=key, cycle=runner)
            all_ok = all_ok and bool(rec.get("ok"))
        elapsed = time.time() - tick
        if all_ok:
            wait, backoff = float(interval), 0
        else:
            runtime_state = _RUNTIME.get("state") or {}
            streak = int(runtime_state.get("consecutive_failures") or 1)
            backoff = min(interval, MIN_INTERVAL_SECONDS * (2 ** min(max(streak - 1, 0), 4)))
            wait = float(min(interval, max(backoff, 5)))
        sleep_for = max(5.0, wait - elapsed)
        _RUNTIME["backoff"] = backoff
        _RUNTIME["next_run"] = time.time() + sleep_for
        _STOP.wait(sleep_for)
        _RUNTIME["next_run"] = None


def start(index: Optional[str] = None,
          interval: Optional[int] = None,
          runner: Optional[Callable[[str], Dict[str, Any]]] = None,
          first_delay: Optional[float] = None) -> Dict[str, Any]:
    """Start the loop (idempotent). Returns :func:`status`."""
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return status()
        keys = [index] if index else indexes()
        ivl = int(interval or interval_seconds())
        delay = float(os.getenv("ZERO_V21_FIRST_DELAY", "20")) if first_delay is None else float(first_delay)
        _STOP.clear()
        _RUNTIME.update({
            "running": True,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "interval": ivl,
            "indexes": list(keys),
            "next_run": time.time() + max(0.0, delay),
            "backoff": 0,
        })
        _THREAD = threading.Thread(target=_loop, args=(list(keys), ivl, runner, max(0.0, delay)),
                                   name="zero-v21-ecosystem", daemon=True)
        _RUNTIME["thread"] = _THREAD.name
        _THREAD.start()
    _log(f"START indexes={','.join(keys)} interval={ivl}s delay={max(0.0, delay):.0f}s")
    return status()


def stop(timeout: float = 5.0) -> Dict[str, Any]:
    """Signal the loop to exit and wait briefly for the thread."""
    global _THREAD
    _STOP.set()
    thread = _THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    with _LOCK:
        _RUNTIME.update({"running": False, "next_run": None})
        _THREAD = None
    _log("STOP")
    return status()


def status() -> Dict[str, Any]:
    thread = _THREAD
    state = _read_state()
    return {
        "enabled": enabled(),
        "running": bool(thread is not None and thread.is_alive()),
        "thread": thread.name if thread is not None else None,
        "interval_seconds": _RUNTIME.get("interval") or interval_seconds(),
        "indexes": list(_RUNTIME.get("indexes") or indexes()),
        "started_at": _RUNTIME.get("started_at"),
        "next_run": _RUNTIME.get("next_run"),
        "backoff_seconds": _RUNTIME.get("backoff") or 0,
        "runs": int(state.get("runs") or 0),
        "failures": int(state.get("failures") or 0),
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "last_run_iso": state.get("last_run_iso"),
        "last_index": state.get("last_index"),
        "last_ok": state.get("last_ok"),
        "last_seconds": state.get("last_seconds"),
        "last_error": state.get("last_error"),
        "last_suggestion": state.get("last_suggestion"),
        "state_path": state_path(),
        "log_path": log_path(),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m engine.v21.scheduler",
                                 description="24/7 timer for ecosystem.run_cycle")
    ap.add_argument("--once", action="store_true", help="run one pass per index and exit")
    ap.add_argument("--loop", action="store_true", help="run the foreground 24/7 loop")
    ap.add_argument("--index", default=None, help="restrict to one index")
    ap.add_argument("--seconds", type=int, default=None, help="override the interval")
    ap.add_argument("--status", action="store_true", help="print scheduler status")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.status:
        print(json.dumps(status(), indent=2, default=str))
        return 0
    if args.loop:
        keys = [args.index] if args.index else indexes()
        ivl = int(args.seconds or interval_seconds())
        print(json.dumps({"starting": True, "indexes": keys, "interval": ivl}, indent=2))
        start(index=None if not args.index else args.index, interval=ivl, first_delay=0)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print(json.dumps(stop(), indent=2, default=str))
        return 0
    keys = [args.index] if args.index else indexes()
    out = [run_once(index=k) for k in keys]
    print(json.dumps(out, indent=2, default=str))
    return 0 if all(r.get("ok") for r in out) else 1


if __name__ == "__main__":
    raise SystemExit(_main())
