"""
ZERO ENGINE — Biological Spike Orchestrator
===========================================

Cold-boots a registered subsystem, executes **one** method, writes an audit
record, then unbinds the instance, drops the module (and any *newly* imported
dependencies) from ``sys.modules``, and runs ``gc.collect()``.

This is a RAM-conservation pattern: heavy stacks (Chroma / MiniLM) stay on
disk until a UI spike asks for them, then they are released.

Usage
-----
    from core.spike_orchestrator import BiologicalSpikeOrchestrator

    orchestrator = BiologicalSpikeOrchestrator()
    out = orchestrator.trigger_spike_activation(
        "monitoring", "local_semantic_retrieval", "margin commentary",
    )
    # out["ok"] is False on failure — this API never raises to the caller
    # (except KeyboardInterrupt / SystemExit).

Registered nodes
----------------
* ``monitoring``       → ``OKFFinancialRAG.local_semantic_retrieval``
* ``decision_engine``  → ``SelfLearningQuantAgent.execute_self_reflection_learning``
* ``risk_mitigation``  → ``SituationalQuantTackle.inspect_market_vitals``

Audit trail: ``db/spike_execution_audit.json`` (atomic replace + file lock).
"""
from __future__ import annotations

import gc
import importlib
import json
import logging
import math
import os
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping, Optional

logger = logging.getLogger("ZERO_SPIKE_ORCHESTRATOR")

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_AUDIT_REL = Path("db") / "spike_execution_audit.json"
_MAX_AUDIT_ENTRIES = 500
_PREVIEW_LIMIT = 2000

# Never flush these even if they appeared during a spike (package parents,
# this module, or the interpreter itself).
_PROTECTED_MODULES = frozenset(
    {
        "core",
        "engine",
        "config",
        "core.spike_orchestrator",
        "builtins",
        "sys",
        "gc",
        "importlib",
        "json",
        "logging",
        "threading",
    }
)

# Shared process runtimes. Flushing them after a spike would break Streamlit /
# Kronos / other tabs that already hold references. Instance unbind + GC is
# what actually releases the MiniLM / Chroma RAM.
_SHARED_RUNTIME_PREFIXES = (
    "numpy",
    "pandas",
    "streamlit",
    "torch",
    "torchvision",
    "requests",
    "urllib3",
    "httpx",
    "plotly",
    "altair",
    "sklearn",
    "scipy",
    "pyarrow",
    "PIL",
    "cv2",
    "google",
    "grpc",
    "pydantic",
    "yaml",
    "dateutil",
    "transformers",
    "tokenizers",
    "huggingface_hub",
    "safetensors",
    "accelerate",
)

# User-facing node key → real module + class (verified against this repo).
# Guessed CamelCase aliases are listed so a mistaken class name still resolves.
SUBSYSTEM_REGISTRY: Dict[str, Dict[str, Any]] = {
    "monitoring": {
        "module": "core.okf_financial_rag",
        "class_name": "OKFFinancialRAG",
        "aliases": ("OkfFinancialQuery", "OKFFinancialRAG"),
        "methods": ("local_semantic_retrieval",),
    },
    "decision_engine": {
        "module": "core.self_learning_agent",
        "class_name": "SelfLearningQuantAgent",
        "aliases": ("SelfLearningAgent", "SelfLearningQuantAgent"),
        "methods": ("execute_self_reflection_learning",),
    },
    "risk_mitigation": {
        "module": "engine.situational_quant",
        "class_name": "SituationalQuantTackle",
        "aliases": ("SituationalQuant", "SituationalQuantTackle"),
        "methods": ("inspect_market_vitals",),
    },
}

_AUDIT_THREAD_LOCK = threading.RLock()


def default_audit_path() -> Path:
    """Absolute path of the spike audit JSON (project-root relative)."""
    return _PROJECT_ROOT / _DEFAULT_AUDIT_REL


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _preview(value: Any, *, limit: int = _PREVIEW_LIMIT) -> Any:
    """JSON-safe, size-capped snapshot of a return value or argument."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, bytes):
        return f"<bytes len={len(value)}>"
    if isinstance(value, tuple):
        return [_preview(item, limit=limit) for item in value[:80]]
    if isinstance(value, list):
        return [_preview(item, limit=min(limit, 400)) for item in value[:80]]
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 80:
                break
            out[str(key)] = _preview(item, limit=min(limit, 400))
        return out
    text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON via temp file + os.replace so readers never see a half file."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(parent), suffix=".tmp", prefix=".spike_audit_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False, default=str)
            handle.write("\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(tmp, str(path))
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _file_lock(lock_path: Path) -> Iterator[None]:
    """Exclusive advisory lock (Windows ``msvcrt`` / POSIX ``fcntl``)."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    locked = False
    try:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            try:
                handle.seek(0)
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _read_audit_list(path: Path) -> list:
    if not path.is_file():
        return []
    try:
        if path.stat().st_size <= 0:
            return []
    except OSError:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        logger.warning("Spike audit unreadable (%s); starting a fresh list.", exc)
        try:
            corrupt = path.with_suffix(path.suffix + ".corrupt")
            os.replace(str(path), str(corrupt))
        except OSError:
            pass
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [raw]
    return []


def _resolve_class(module: Any, spec: Mapping[str, Any]) -> type:
    """Return the real class object using the registry name, then aliases."""
    candidates: list[str] = []
    primary = str(spec.get("class_name") or "").strip()
    if primary:
        candidates.append(primary)
    aliases = spec.get("aliases") or ()
    for alias in aliases:
        name = str(alias).strip()
        if name and name not in candidates:
            candidates.append(name)

    for name in candidates:
        obj = getattr(module, name, None)
        if isinstance(obj, type):
            return obj

    defined = [
        name
        for name, obj in vars(module).items()
        if isinstance(obj, type) and getattr(obj, "__module__", None) == module.__name__
    ]
    raise AttributeError(
        f"Module {module.__name__!r} has none of {candidates}. "
        f"Classes defined there: {defined or '(none)'}"
    )


def _snapshot_modules() -> set[str]:
    return set(sys.modules.keys())


def _unbind_from_parent(module_name: str) -> None:
    """Drop the submodule attribute on the parent package so GC can reclaim it."""
    parent_name, _, child = module_name.rpartition(".")
    if not parent_name or not child:
        return
    parent = sys.modules.get(parent_name)
    if parent is None:
        return
    try:
        attr = getattr(parent, child, None)
        cached = sys.modules.get(module_name)
        if attr is cached or cached is None:
            delattr(parent, child)
    except Exception:
        logger.debug("Could not unbind %s from %s", child, parent_name, exc_info=True)


def _is_shared_runtime(module_name: str) -> bool:
    root = module_name.split(".", 1)[0]
    return root in _SHARED_RUNTIME_PREFIXES or any(
        root == prefix or root.startswith(prefix + "_")
        for prefix in _SHARED_RUNTIME_PREFIXES
    )


def _flush_modules(snapshot: set[str], target_module: str) -> list[str]:
    """Drop the target module plus spike-only imports.

    Pre-existing ``core.*`` / ``engine.*`` siblings stay loaded. Package
    parents and shared runtimes (numpy, transformers, Streamlit, …) are
    never flushed.
    """
    current = set(sys.modules.keys())
    added = current - snapshot
    victims = set()
    victims.add(target_module)
    for name in added:
        if name in _PROTECTED_MODULES or _is_shared_runtime(name):
            continue
        victims.add(name)
    victims -= _PROTECTED_MODULES

    flushed: list[str] = []
    # Children first (longest name) so package parents go last if present.
    for name in sorted(victims, key=len, reverse=True):
        if name in _PROTECTED_MODULES:
            continue
        try:
            _unbind_from_parent(name)
            sys.modules.pop(name, None)
            flushed.append(name)
        except Exception:
            logger.debug("Could not pop sys.modules[%r]", name, exc_info=True)
    return flushed


class BiologicalSpikeOrchestrator:
    """On-demand subsystem loader: import → instantiate → call → unload → GC."""

    def __init__(
        self,
        audit_path: str | os.PathLike[str] | None = None,
        registry: Optional[Mapping[str, Mapping[str, Any]]] = None,
        *,
        reload_if_present: bool = True,
    ) -> None:
        if audit_path is None:
            self.audit_path = default_audit_path()
        else:
            path = Path(audit_path).expanduser()
            self.audit_path = path if path.is_absolute() else (_PROJECT_ROOT / path)
        self.registry: Dict[str, Dict[str, Any]] = {
            str(key): dict(value) for key, value in (registry or SUBSYSTEM_REGISTRY).items()
        }
        self.reload_if_present = bool(reload_if_present)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def registered_nodes(self) -> list[str]:
        return sorted(self.registry.keys())

    def trigger_spike_activation(
        self,
        system_node_key: str,
        execution_method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Cold-boot ``system_node_key``, call ``execution_method_name``, unload.

        Returns a dict with ``ok``, ``result``, and error fields. Never raises
        for subsystem failures (Streamlit-safe). ``KeyboardInterrupt`` /
        ``SystemExit`` still propagate.
        """
        started = time.perf_counter()
        key = str(system_node_key or "").strip()
        method_name = str(execution_method_name or "").strip()
        record: Dict[str, Any] = {
            "timestamp": _utc_now_iso(),
            "system_node_key": key,
            "execution_method_name": method_name,
            "args_preview": _preview(args),
            "kwargs_preview": _preview(kwargs),
            "ok": False,
            "result_preview": None,
            "error": None,
            "error_type": None,
            "traceback": None,
            "class_name": None,
            "module_path": None,
            "flushed_modules": [],
            "elapsed_ms": None,
        }
        result: Any = None
        snapshot = _snapshot_modules()
        module = None
        instance = None
        target_module = ""

        try:
            spec = self.registry.get(key)
            if not spec:
                known = ", ".join(self.registered_nodes()) or "(none)"
                raise KeyError(
                    f"Unknown spike node {key!r}. Registered nodes: {known}."
                )
            if not method_name:
                raise ValueError("execution_method_name is empty.")

            target_module = str(spec["module"])
            record["module_path"] = target_module
            module = self._load_module(target_module)
            cls = _resolve_class(module, spec)
            record["class_name"] = cls.__name__
            instance = cls()
            method = getattr(instance, method_name, None)
            if method is None or not callable(method):
                public = sorted(
                    name
                    for name in dir(instance)
                    if not name.startswith("_") and callable(getattr(instance, name, None))
                )
                raise AttributeError(
                    f"{cls.__name__} has no callable {method_name!r}. "
                    f"Public methods: {public or '(none)'}"
                )
            result = method(*args, **kwargs)
            record["ok"] = True
            record["result_preview"] = _preview(result)
            logger.info(
                "Spike %s.%s completed (%s)",
                key,
                method_name,
                cls.__name__,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            record["ok"] = False
            record["error"] = str(exc) or exc.__class__.__name__
            record["error_type"] = type(exc).__name__
            record["traceback"] = traceback.format_exc()
            logger.error(
                "Spike %s.%s failed: %s",
                key,
                method_name,
                exc,
                exc_info=True,
            )
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
            try:
                instance = None
                module = None
                if target_module:
                    record["flushed_modules"] = _flush_modules(snapshot, target_module)
            except Exception:
                logger.exception("Spike teardown failed for %s", key)
            try:
                gc.collect()
            except Exception:
                logger.debug("gc.collect() failed", exc_info=True)
            try:
                self._append_audit(record)
            except Exception:
                logger.exception("Spike audit write failed for %s", key)

        return {
            "ok": bool(record["ok"]),
            "system_node_key": key,
            "execution_method_name": method_name,
            "class_name": record["class_name"],
            "module_path": record["module_path"],
            "result": result,
            "error": record["error"],
            "error_type": record["error_type"],
            "traceback": record["traceback"],
            "elapsed_ms": record["elapsed_ms"],
            "audit_path": str(self.audit_path),
        }

    def _load_module(self, module_path: str):
        existing = sys.modules.get(module_path)
        if existing is not None and self.reload_if_present:
            try:
                return importlib.reload(existing)
            except Exception as exc:
                logger.warning(
                    "Reload of %s failed (%s); using already-loaded module.",
                    module_path,
                    exc,
                )
                return existing
        return importlib.import_module(module_path)

    def _append_audit(self, record: Mapping[str, Any]) -> None:
        path = self.audit_path
        lock_path = path.with_name(path.name + ".lock")
        with _AUDIT_THREAD_LOCK:
            with _file_lock(lock_path):
                entries = _read_audit_list(path)
                entries.append(dict(record))
                if len(entries) > _MAX_AUDIT_ENTRIES:
                    entries = entries[-_MAX_AUDIT_ENTRIES:]
                _atomic_write_json(path, entries)


__all__ = [
    "BiologicalSpikeOrchestrator",
    "SUBSYSTEM_REGISTRY",
    "default_audit_path",
]
