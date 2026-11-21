"""
ZERO Engine v2.0 — REA Bridge
=============================

REA (``rea-agents``, MIT) is a reverse-engineering MCP server: one interface for
inspecting binaries, Electron/JS bundles, HTTP APIs, native UI and runtime
behaviour. The desk asked the engine to learn how institutional research tools
work and to reuse that capability.

The honest integration is therefore **MCP**, not a copy of REA's internals:

* ZERO registers ``rea`` in ``config/mcp_servers.json`` (already done).
* ``probe_rea()`` performs the MCP handshake and caches the tool catalogue, so
  the engine knows exactly which capabilities are available on this machine.
* ``reverse_engineer(target)`` picks the best matching tool from that catalogue
  and calls it. Tool selection is by capability keywords, never by guessing a
  tool name.
* Everything degrades to a clear "not available" status when Node/npx or the
  package is missing. No fabricated analysis is ever returned.

The result is stored in ``db/v2/rea_tools.json`` and surfaced in the v2.0 panel
under **MCP / REA**.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
from typing import Any, Dict, List, Optional

from engine.v2 import mcp_client

SCHEMA_VERSION = "2.0"
CACHE_PATH = os.path.join("db", "v2", "rea_tools.json")
DEFAULT_REA_PATH = r"D:\D all\charan\personal\REA"

# Capability buckets the engine cares about, mapped to keywords found in tool
# names/descriptions. Ordered by preference inside each bucket.
CAPABILITY_KEYWORDS: Dict[str, List[str]] = {
    "binary": ["decompil", "disassembl", "binary", "ghidra", "ida", "hexrays"],
    "javascript": ["electron", "javascript", "bundle", "webpack", "asar", "sourcemap"],
    "http": ["http", "endpoint", "api", "request", "har", "route"],
    "native_ui": ["native", "ui", "swift", "accessibility", "window"],
    "runtime": ["trace", "frida", "debug", "runtime", "process", "lldb"],
    "search": ["search", "grep", "find", "symbol", "string"],
}


def _read_json(path: str) -> Any:
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


def rea_path() -> Optional[str]:
    """Locate the local REA checkout, if one exists."""
    env = os.getenv("ZERO_REA_PATH", "").strip()
    for candidate in (env, DEFAULT_REA_PATH):
        if candidate and os.path.isdir(candidate):
            return candidate
    return None


def environment() -> Dict[str, Any]:
    """Report what is needed and what is present. No process is started."""
    path = rea_path()
    node = shutil.which("node")
    npx = shutil.which("npx")
    version = None
    pkg = os.path.join(path, "package.json") if path else ""
    if pkg and os.path.isfile(pkg):
        data = _read_json(pkg) or {}
        version = data.get("version") if isinstance(data, dict) else None
    return {
        "schema": SCHEMA_VERSION,
        "rea_path": path,
        "package_version": version,
        "node": node,
        "npx": npx,
        "ready": bool(npx),
        "notes": [] if npx else [
            "npx was not found on PATH. Install Node.js >= 22 to enable the REA bridge.",
        ],
    }


def cached_tools() -> Optional[Dict[str, Any]]:
    data = _read_json(CACHE_PATH)
    return data if isinstance(data, dict) else None


def probe_rea(timeout: float = 30.0) -> Dict[str, Any]:
    """Handshake with REA and cache its tool catalogue."""
    out = mcp_client.probe("rea", timeout=timeout)
    out["environment"] = environment()
    if out.get("ok"):
        buckets: Dict[str, List[str]] = {}
        for tool in out.get("tools", []):
            blob = f"{tool.get('name','')} {tool.get('description','')}".lower()
            for bucket, keywords in CAPABILITY_KEYWORDS.items():
                if any(k in blob for k in keywords):
                    buckets.setdefault(bucket, []).append(tool.get("name"))
        out["capabilities"] = {k: sorted(set(v)) for k, v in buckets.items()}
        out["guidance"] = []
    else:
        out["capabilities"] = {}
        out["guidance"] = [
            "REA was not reachable. Check `npx -y rea-agents@6.10.1 mcp` runs on this machine.",
            "The server's own stderr is shown in the panel; it names the exact cause (for example a Node version requirement).",
            "The rest of the engine does not depend on REA; this panel stays in NOT CONNECTED.",
        ]
    # Cache failures too, so the panel can explain *why* instead of showing a
    # bare NOT_CONNECTED after every restart.
    _write_json(CACHE_PATH, out)
    return out


def _pick_tool(tools: List[Dict[str, Any]], intent: str) -> Optional[str]:
    """Choose the tool whose name/description best matches the intent."""
    keywords = CAPABILITY_KEYWORDS.get(intent, []) + [intent]
    best: Optional[tuple] = None
    for tool in tools:
        name = str(tool.get("name") or "")
        blob = f"{name} {tool.get('description','')}".lower()
        score = sum(1 for k in keywords if k and k in blob)
        if score and (best is None or score > best[0]):
            best = (score, name)
    return best[1] if best else None


def reverse_engineer(target: str, intent: str = "binary", extra_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Run one REA analysis.

    ``target`` is a path or URL, never executed by ZERO — it is passed to the
    MCP tool as an argument. Arguments are best-effort: the tool's declared
    input schema is inspected and only known keys are populated.
    """
    if not target or not str(target).strip():
        return {"ok": False, "error": "empty target", "server": "rea"}
    tools_cache = cached_tools()
    tools = (tools_cache or {}).get("tools") or []
    if not tools:
        probed = probe_rea()
        tools = probed.get("tools") or []
        if not probed.get("ok"):
            return {"ok": False, "error": probed.get("error") or "REA unavailable",
                    "server": "rea", "environment": probed.get("environment")}

    tool_name = _pick_tool(tools, intent)
    if not tool_name:
        return {
            "ok": False, "server": "rea",
            "error": f"no REA tool advertises the {intent!r} capability",
            "available_tools": [t.get("name") for t in tools],
        }

    tool = next((t for t in tools if t.get("name") == tool_name), {})
    schema = (tool.get("input_schema") or {})
    props = schema.get("properties") if isinstance(schema, dict) else {}
    args: Dict[str, Any] = {}
    for key in ("target", "path", "file", "url", "query", "input"):
        if isinstance(props, dict) and key in props:
            args[key] = str(target)
            break
    for key in ("mode", "depth", "analysis"):
        if isinstance(props, dict) and key in props and extra_args and key in extra_args:
            args[key] = extra_args[key]
    if not args:
        # Schema not advertised: pass the conventional key and let the server
        # reject it, rather than silently calling with nothing.
        args["target"] = str(target)
    if extra_args:
        for k, v in extra_args.items():
            if isinstance(props, dict) and k in props:
                args[k] = v

    result = mcp_client.call("rea", tool_name, args, timeout=120.0)
    result["intent"] = intent
    result["arguments"] = args
    return result


def status() -> Dict[str, Any]:
    """Cheap status for the panel (uses cache, never launches a process)."""
    env = environment()
    cache = cached_tools()
    if cache and cache.get("ok"):
        return {
            "state": "CONNECTED",
            "environment": env,
            "tool_count": len(cache.get("tools") or []),
            "capabilities": cache.get("capabilities") or {},
            "checked_at": cache.get("checked_at"),
            "server_info": cache.get("server_info") or {},
        }
    return {
        "state": "NOT_CONNECTED" if not cache else "LAST_ATTEMPT_FAILED",
        "environment": env,
        "tool_count": 0,
        "capabilities": {},
        "checked_at": (cache or {}).get("checked_at"),
        "error": (cache or {}).get("error"),
        "stderr_tail": (cache or {}).get("stderr_tail") or [],
    }


def capability_summary() -> List[str]:
    """Human-readable capability list for the research desks."""
    st = status()
    lines: List[str] = []
    for bucket, tools in (st.get("capabilities") or {}).items():
        lines.append(f"{bucket}: {', '.join(tools)}")
    return lines
