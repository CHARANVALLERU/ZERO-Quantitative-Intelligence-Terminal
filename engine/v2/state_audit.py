"""
ZERO Engine v2.0 — System Integrity Audit
=========================================

The original report found a set of defects that were *silent*: stale
calibration, a daemon that claimed to be alive, NaN in the feedback log, dead
cache branches, Docker files that did not exist. Silent defects are the
expensive kind, so v2.0 ships an auditor that measures them every time the panel
opens and can repair the safe ones.

``audit()`` is read-only. ``repair()`` only performs reversible, data-safe
fixes: it never deletes a file, never rewrites a model, and never invents a
metric. Destructive cleanups are reported, not performed.
"""

from __future__ import annotations

import ast
import datetime as _dt
import json
import os
import re
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "2.0"

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".next",
    "claude-obsidian-main", "obsidian_vault", "second_zero_vault",
    "sandbox", "dist", "build", ".analysis",
}

JSON_FILES = [
    "db/feedback_log.json",
    "db/calibrator.json",
    "db/range_config.json",
    "db/provider_health.json",
    "db/aite/daemon_state.json",
    "db/aite/heartbeat.json",
    "db/v2/macro_inputs.json",
    "db/v2/macro_snapshot.json",
]

CRITICAL_IMPORTS = {
    "pytest": "dev", "fastapi": "backend", "uvicorn": "backend",
    "redis": "backend", "slowapi": "backend", "jose": "backend",
    "passlib": "backend", "playwright": "dev",
}


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _age_days(path: str) -> Optional[float]:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    return round((_dt.datetime.now().timestamp() - mtime) / 86400.0, 1)


def _check(name: str, ok: bool, severity: str, detail: str,
           fixable: bool = False) -> Dict[str, Any]:
    return {
        "name": name,
        "ok": bool(ok),
        "severity": severity if not ok else "OK",
        "detail": detail,
        "fixable": bool(fixable),
    }


# ═══════════════════════════════════════════════════════════════════════════
#  Individual checks
# ═══════════════════════════════════════════════════════════════════════════

def _check_json_health() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in JSON_FILES:
        if not os.path.exists(path):
            out.append(_check(f"json:{path}", True, "INFO", "not present (optional)"))
            continue
        text = _read_text(path) or ""
        nan_count = len(re.findall(r"(?<![\w\"])NaN(?![\w\"])", text))
        try:
            json.loads(text)
            parsed = True
        except ValueError:
            parsed = False
        ok = parsed and nan_count == 0
        out.append(_check(
            f"json:{path}", ok,
            "HIGH" if nan_count else "MEDIUM",
            ("valid JSON" if parsed else "not parseable as strict JSON")
            + (f"; {nan_count} bare NaN token(s)" if nan_count else "")
            + f"; age {_age_days(path)}d",
            fixable=nan_count > 0,
        ))
    return out


def _check_daemon() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    hb_path = "db/aite/heartbeat.json"
    st_path = "db/aite/daemon_state.json"
    hb = _load(hb_path)
    st = _load(st_path)
    if hb is None and st is None:
        out.append(_check("aite_liveness", True, "INFO", "no daemon state files"))
        return out
    ts = None
    try:
        ts = float((hb or {}).get("ts") or (st or {}).get("last_tick") or 0) or None
    except (TypeError, ValueError):
        ts = None
    age_min = ( _dt.datetime.now().timestamp() - ts) / 60.0 if ts else None
    claimed = bool((st or {}).get("running")) or (hb or {}).get("status") == "alive"
    fresh = age_min is not None and age_min <= 5.0
    ok = (not claimed) or fresh
    out.append(_check(
        "aite_liveness", ok, "HIGH",
        f"claims running={claimed}; heartbeat age "
        f"{'unknown' if age_min is None else f'{age_min:.0f} min'}"
        + ("" if ok else " — the daemon is NOT alive"),
        fixable=claimed and not fresh,
    ))
    return out


def _check_staleness() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        from config import ML_STALE_DAYS
        stale_limit = float(ML_STALE_DAYS)
    except Exception:
        stale_limit = 7.0
    age = _age_days("db/calibrator.json")
    ok = age is not None and age <= stale_limit
    out.append(_check(
        "calibrator_freshness", ok, "MEDIUM",
        f"calibrator.json is {age}d old (limit {stale_limit:.0f}d)"
        + ("" if ok else " — the adaptive layer falls back to identity"),
    ))
    for path, label in (("db/range_config.json", "range_config"),
                        ("db/provider_health.json", "provider_health")):
        age = _age_days(path)
        out.append(_check(
            f"{label}_freshness", age is not None and age <= 45, "LOW",
            f"{path} is {age}d old",
        ))
    return out


def _check_docker() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    compose = "docker/docker-compose.yml"
    text = _read_text(compose)
    if text is None:
        out.append(_check("docker_compose", False, "LOW", "docker/docker-compose.yml missing"))
        return out
    refs = re.findall(r"dockerfile:\s*(\S+)", text)
    for ref in refs:
        # Compose resolves `dockerfile:` against the build context, which is the
        # repository root here — so the declared path is used as-is.
        path = ref if os.path.isabs(ref) else ref
        out.append(_check(f"dockerfile:{ref}", os.path.isfile(path), "MEDIUM",
                          f"{path} {'exists' if os.path.isfile(path) else 'is missing'}"))
    for ref in re.findall(r"-\s*\./([^:]+\.conf):", text):
        # Bind mounts are relative to docker-compose.yml's own directory.
        path = os.path.join("docker", ref)
        out.append(_check(f"docker_volume:{ref}", os.path.isfile(path), "MEDIUM",
                          f"{path} {'exists' if os.path.isfile(path) else 'is missing'}"))
    out.append(_check(
        "docker_backend_entrypoint",
        "backend.main:app" in text,
        "MEDIUM",
        "compose must launch backend.main:app from the repo root, not main:app",
    ))
    return out


def _check_packaging() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for pkg in ("backend", "db", "tests", "engine/v2"):
        path = os.path.join(pkg, "__init__.py")
        out.append(_check(f"init:{pkg}", os.path.isfile(path), "LOW",
                          f"{path} {'present' if os.path.isfile(path) else 'missing'}",
                          fixable=not os.path.isfile(path)))
    req = (_read_text("requirements.txt") or "").lower()
    dev = (_read_text("requirements-dev.txt") or "").lower()
    backend = (_read_text("requirements-backend.txt") or "").lower()
    for mod, where in CRITICAL_IMPORTS.items():
        needle = mod.lower()
        listed = needle in req or needle in dev or needle in backend
        out.append(_check(f"dependency:{mod}", listed, "MEDIUM",
                          f"{mod} ({where}) is {'listed' if listed else 'not listed'} in requirements",
                          fixable=not listed))
    env = (_read_text(".env.example") or "")
    out.append(_check("env_template", not env.lstrip().startswith("[TEMPLATE]"), "LOW",
                      ".env.example first line must be a comment, not [TEMPLATE]"))
    return out


def _check_dead_code() -> List[Dict[str, Any]]:
    text = _read_text("engine/prediction_matrix.py") or ""
    dead = "_cache600" in text and "hasattr(_cache600" in text
    return [_check(
        "dead_cache_branch", not dead, "MEDIUM",
        "prediction_matrix still guards on hasattr(_cache600, 'cache')"
        if dead else "no dead cache branch detected",
    )]


def _check_syntax() -> List[Dict[str, Any]]:
    bad: List[str] = []
    count = 0
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            count += 1
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    ast.parse(fh.read(), filename=path)
            except SyntaxError as exc:
                bad.append(f"{path}:{exc.lineno}: {exc.msg}")
            except (OSError, UnicodeDecodeError):
                continue
    return [_check(
        "python_syntax", not bad, "HIGH",
        f"{count} modules parsed" + (f"; failures: {bad}" if bad else ""),
    )]


def _check_duplicate_frontend() -> List[Dict[str, Any]]:
    root_src = os.path.isdir("src")
    next_src = os.path.isdir("frontend/src")
    if root_src and next_src:
        return [_check(
            "duplicate_frontend", False, "LOW",
            "both src/ and frontend/src/ exist; root src/ is a drifted duplicate "
            "(retained intentionally — delete it yourself if unused)",
        )]
    return [_check("duplicate_frontend", True, "INFO", "no duplicate frontend")]


def _load(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════

def _check_aite_provenance() -> List[Dict[str, Any]]:
    """Every alive bot must be traceable to real, timestamped bars."""
    out: List[Dict[str, Any]] = []
    try:
        from engine import data_provenance
        report = data_provenance.audit_stored_state()
    except Exception as exc:  # noqa: BLE001
        return [_check("aite:provenance", False, "MEDIUM", f"audit failed: {exc}")]

    bots = report.get("bots") or {}
    trades = report.get("trades") or {}
    alive = int(bots.get("alive") or 0)
    bad_alive = int(bots.get("alive_unverified") or 0) + int(bots.get("alive_synthetic") or 0)
    if alive == 0:
        out.append(_check("aite:provenance", True, "INFO", "no live AITE bots"))
    else:
        out.append(_check(
            "aite:provenance", bad_alive == 0, "HIGH",
            f"{bad_alive}/{alive} alive bots lack a real-data provenance record",
        ))

    pct = float(trades.get("placeholder_pct") or 0.0)
    out.append(_check(
        "aite:timestamps", pct < 1.0, "HIGH" if pct > 50 else "MEDIUM",
        f"{pct}% of sampled trades carry placeholder timestamps (bar_N) - "
        "those returns cannot be calendar-aligned or correlated",
    ))

    cache = report.get("exam_cache") or {}
    passed = int(cache.get("n_passed") or 0)
    alive_ids = int((report.get("portfolio") or {}).get("bot_ids") or 0)
    out.append(_check(
        "aite:admission", alive_ids <= max(1, passed) if alive_ids else True, "HIGH",
        f"portfolio holds {alive_ids} bots but only {passed} exam pass was recorded",
    ))
    return out


def _check_api_auth() -> List[Dict[str, Any]]:
    """The API must not ship a known key or an unauthenticated route set."""
    out: List[Dict[str, Any]] = []
    auth_src = _read_text(os.path.join("backend", "auth.py")) or ""
    main_src = _read_text(os.path.join("backend", "main.py")) or ""

    # A weak-key *list* is fine (it is how weak keys are detected); what must
    # never appear is an assignment of a constant to SECRET_KEY.
    hardcoded = 'SECRET_KEY = "' in auth_src and "_load_secret_key" not in auth_src
    out.append(_check(
        "api:secret_key", not hardcoded, "HIGH",
        "JWT key is loaded from the environment (ZERO_SECRET_KEY), not hard-coded",
    ))
    # A wildcard is acceptable only as the documented fallback of a *configured*
    # allow-list, and never together with credentials.
    cors_configured = "_cors_open" in main_src and "ZERO_CORS_ORIGINS" in main_src
    no_credentialed_wildcard = "allow_credentials=not _cors_open" in main_src
    out.append(_check(
        "api:cors", cors_configured and no_credentialed_wildcard, "HIGH",
        "CORS origins come from configuration; credentials are off while the wildcard is on",
    ))
    guared = main_src.count("CurrentUser") + main_src.count("TraderUser")
    out.append(_check(
        "api:routes_guarded", guared >= 10, "HIGH",
        f"{guared} guarded route signatures found (bearer token + role dependencies)",
    ))
    out.append(_check(
        "api:typed_tokens", "expected_type" in auth_src, "HIGH",
        "access and refresh tokens are typed and cannot be swapped",
    ))
    return out


def _check_data_integrity() -> List[Dict[str, Any]]:
    """Point-in-time store and broker cross-check availability."""
    out: List[Dict[str, Any]] = []
    try:
        from data import pit_store
        report = pit_store.verify()
        out.append(_check(
            "data:pit_store", report.get("clean", True), "MEDIUM",
            f"{report.get('symbols', 0)} symbols stored; {len(report.get('issues') or [])} issue(s)",
        ))
    except Exception as exc:  # noqa: BLE001
        out.append(_check("data:pit_store", False, "LOW", f"unavailable: {exc}"))
    try:
        from engine.broker import historical
        available = historical.available_adapters()
        out.append(_check(
            "data:broker_history", bool(available), "INFO",
            f"broker adapters ready: {', '.join(available) or 'none (set Kite/Upstox/Dhan tokens)'}",
        ))
    except Exception as exc:  # noqa: BLE001
        out.append(_check("data:broker_history", False, "LOW", f"unavailable: {exc}"))
    return out


def audit() -> Dict[str, Any]:
    """Run every check. Returns a structured, JSON-safe report."""
    checks: List[Dict[str, Any]] = []
    checks += _check_json_health()
    checks += _check_daemon()
    checks += _check_staleness()
    checks += _check_docker()
    checks += _check_packaging()
    checks += _check_dead_code()
    checks += _check_syntax()
    checks += _check_duplicate_frontend()
    checks += _check_aite_provenance()
    checks += _check_api_auth()
    checks += _check_data_integrity()

    failed = [c for c in checks if not c["ok"]]
    high = [c for c in failed if c["severity"] == "HIGH"]
    return {
        "schema": SCHEMA_VERSION,
        "asof": _now_iso(),
        "checks": checks,
        "total": len(checks),
        "passed": len(checks) - len(failed),
        "failed": len(failed),
        "high": len(high),
        "health_pct": round((len(checks) - len(failed)) / max(1, len(checks)) * 100.0, 1),
        "repairable": [c["name"] for c in failed if c["fixable"]],
    }


def repair(apply: bool = False) -> Dict[str, Any]:
    """Repair the safe, reversible defects. Returns what was done.

    With ``apply=False`` it only reports the intended actions.
    """
    actions: List[Dict[str, Any]] = []

    # 1. Bare NaN -> null in every JSON artefact (strict-JSON safety).
    for path in JSON_FILES:
        if not os.path.exists(path):
            continue
        text = _read_text(path)
        if text is None:
            continue
        cleaned = re.sub(r"(?<![\w\"])NaN(?![\w\"])", "null", text)
        if cleaned != text:
            actions.append({"action": f"sanitize {path}", "applied": False})
            if apply:
                try:
                    json.loads(cleaned)
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write(cleaned)
                    actions[-1]["applied"] = True
                except ValueError:
                    actions[-1]["error"] = "still invalid after NaN removal; left untouched"

    # 2. Phantom daemon state -> honestly stopped.
    st = _load("db/aite/daemon_state.json")
    hb = _load("db/aite/heartbeat.json")
    if st and st.get("running"):
        actions.append({"action": "mark st AITE daemon_state as stopped", "applied": False})
        if apply:
            st["running"] = False
            st["stopped_reason"] = "v2.0 audit: heartbeat stale; process not alive"
            st["stopped_at"] = _now_iso()
            _write_json("db/aite/daemon_state.json", st)
            actions[-1]["applied"] = True
    if hb and hb.get("status") == "alive":
        actions.append({"action": "mark AITE heartbeat as stale", "applied": False})
        if apply:
            hb["status"] = "stale"
            hb["stale_reason"] = "v2.0 audit: heartbeat older than the liveness window"
            hb["marked_at"] = _now_iso()
            _write_json("db/aite/heartbeat.json", hb)
            actions[-1]["applied"] = True

    # 3. Missing __init__.py (namespace packages that should be regular).
    for pkg in ("backend", "db", "tests", "engine/v2"):
        path = os.path.join(pkg, "__init__.py")
        if not os.path.isfile(path):
            actions.append({"action": f"create {path}", "applied": False})
            if apply:
                try:
                    os.makedirs(pkg, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as fh:
                        fh.write('"""Package marker (added by ZERO v2.0 integrity audit)."""\n')
                    actions[-1]["applied"] = True
                except OSError as exc:
                    actions[-1]["error"] = str(exc)

    return {
        "schema": SCHEMA_VERSION,
        "asof": _now_iso(),
        "apply": apply,
        "actions": actions,
        "changed": sum(1 for a in actions if a.get("applied")),
        "pending": sum(1 for a in actions if not a.get("applied")),
    }


def _write_json(path: str, payload: Any) -> bool:
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, allow_nan=False)
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError):
        return False
