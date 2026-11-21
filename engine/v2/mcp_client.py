"""
ZERO Engine v2.0 — MCP Client & Registry
========================================

A small, dependency-free **Model Context Protocol** client so the engine can
consume research from external MCP servers instead of hard-coding scrapers.

Why it exists: the desk asked for a connector where multiple MCP providers can
be attached so the engine can pull data and research. Rather than inventing a
bespoke plugin format, this speaks MCP's JSON-RPC 2.0 transport over stdio —
the same transport REA (`npx rea-agents mcp`) and most other servers expose.

Safety:

* Servers are declared in ``config/mcp_servers.json``; nothing is started until
  a caller explicitly asks for it.
* Every call has a timeout and the child process is always terminated.
* ``enabled: false`` servers are never launched.
* Tool results are treated as **data**, truncated, and never executed.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "2.0"
REGISTRY_PATH = os.path.join("config", "mcp_servers.json")
DEFAULT_TIMEOUT = 25.0
MAX_RESULT_CHARS = 20_000

_DEFAULT_REGISTRY = {
    "schema": SCHEMA_VERSION,
    "servers": {
        "rea": {
            "command": "npx",
            "args": ["-y", "rea-agents@6.10.1", "mcp"],
            "enabled": True,
            "auto_start": False,
            "purpose": "Reverse-engineering MCP: inspect binaries, Electron/JS bundles, HTTP APIs and native UI.",
            "notes": "Set enabled=false to keep the engine fully offline.",
        }
    },
}


class MCPError(RuntimeError):
    """Raised for transport, protocol or server-reported failures."""


def load_registry() -> Dict[str, Any]:
    try:
        with open(REGISTRY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("servers"), dict):
            return data
    except (OSError, ValueError):
        pass
    return json.loads(json.dumps(_DEFAULT_REGISTRY))


def save_registry(registry: Dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(REGISTRY_PATH) or ".", exist_ok=True)
        tmp = REGISTRY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(registry, fh, indent=2)
        os.replace(tmp, REGISTRY_PATH)
        return True
    except (OSError, ValueError):
        return False


class MCPClient:
    """One stdio MCP server connection."""

    def __init__(self, command: str, args: Optional[List[str]] = None,
                 env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.command = command
        self.args = list(args or [])
        self.env = {**os.environ, **(env or {})}
        self.cwd = cwd
        self.timeout = timeout
        self._proc: Optional[subprocess.Popen] = None
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._next_id = 1
        self._lock = threading.Lock()
        self.server_info: Dict[str, Any] = {}
        self.stderr_tail: List[str] = []

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self) -> "MCPClient":
        if self._proc is not None:
            return self
        try:
            self._proc = subprocess.Popen(
                self._launch_argv(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self.env,
                cwd=self.cwd,
            )
        except FileNotFoundError as exc:
            raise MCPError(f"{self.command!r} not found on PATH: {exc}") from exc
        except OSError as exc:
            raise MCPError(f"failed to launch {self.command!r}: {exc}") from exc

        threading.Thread(target=self._read_stdout, daemon=True, name="mcp-stdout").start()
        threading.Thread(target=self._read_stderr, daemon=True, name="mcp-stderr").start()
        return self

    def _launch_argv(self) -> Any:
        """Build a launch spec that Windows can actually execute.

        ``npx``/``uvx`` are ``.CMD`` shims on Windows, and CreateProcess cannot
        run them directly. The reliable route is to invoke npm's JS entry point
        with ``node``. If that file is absent we fall back to a single
        pre-quoted ``cmd.exe /c`` string, because Popen escapes embedded quotes
        when it is handed a list. On POSIX this just resolves the binary.
        """
        exe = shutil.which(self.command) or self.command
        if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
            stem = os.path.splitext(os.path.basename(exe))[0]
            npm_cli = os.path.join(
                os.path.dirname(exe), "node_modules", "npm", "bin", f"{stem}-cli.js"
            )
            node = shutil.which("node")
            if node and os.path.isfile(npm_cli):
                return [node, npm_cli, *self.args]
            quoted = subprocess.list2cmdline([exe, *self.args])
            return f'cmd.exe /c "{quoted}"'
        return [exe, *self.args]

    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            self._queue.put(line)

    def _read_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            self.stderr_tail.append(line.rstrip()[:400])
            del self.stderr_tail[:-40]

    def stop(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def __enter__(self) -> "MCPClient":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    # ── transport ─────────────────────────────────────────────────────────
    def _send(self, payload: Dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError("client is not started")
        try:
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"write failed: {exc}") from exc

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        with self._lock:
            req_id = self._next_id
            self._next_id += 1
            self._send({
                "jsonrpc": "2.0", "id": req_id, "method": method,
                "params": params or {},
            })
            deadline = time.time() + self.timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise MCPError(self._timeout_message(method))
                try:
                    line = self._queue.get(timeout=remaining)
                except queue.Empty:
                    raise MCPError(self._timeout_message(method))
                line = (line or "").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue  # server chatter, not JSON-RPC
                if msg.get("id") != req_id:
                    continue
                if "error" in msg:
                    err = msg["error"] or {}
                    raise MCPError(str(err.get("message") or err)[:500])
                return msg.get("result")

    def _timeout_message(self, method: str) -> str:
        """Timeout errors must carry the child's own diagnosis.

        A silent timeout hides the actual cause (wrong Node version, missing
        package, bad flags). The server's last stderr line is the actionable
        part, so it is attached to the exception.
        """
        if self._proc is not None and self._proc.poll() is not None:
            tail = self.stderr_tail[-1] if self.stderr_tail else "no stderr output"
            return f"server exited ({self._proc.returncode}) before {method}: {tail[:400]}"
        tail = f"; server stderr: {self.stderr_tail[-1][:400]}" if self.stderr_tail else ""
        return f"timeout waiting for {method}{tail}"

    # ── protocol ──────────────────────────────────────────────────────────
    def initialize(self) -> Dict[str, Any]:
        result = self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ZERO-Engine", "version": SCHEMA_VERSION},
        })
        self.server_info = result if isinstance(result, dict) else {}
        try:
            self.notify("notifications/initialized", {})
        except MCPError:
            pass
        return self.server_info

    def list_tools(self) -> List[Dict[str, Any]]:
        result = self.request("tools/list", {})
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return result if isinstance(result, dict) else {"raw": result}


def client_for(server_id: str, timeout: float = DEFAULT_TIMEOUT) -> MCPClient:
    """Build (but do not start) a client for a registered server."""
    registry = load_registry()
    cfg = (registry.get("servers") or {}).get(server_id)
    if not isinstance(cfg, dict):
        raise MCPError(f"server {server_id!r} is not registered in {REGISTRY_PATH}")
    if not cfg.get("enabled", False):
        raise MCPError(f"server {server_id!r} is disabled in {REGISTRY_PATH}")
    command = cfg.get("command")
    if not command:
        raise MCPError(f"server {server_id!r} has no command")
    return MCPClient(
        command=command,
        args=cfg.get("args") or [],
        env=cfg.get("env") or {},
        cwd=cfg.get("cwd") or None,
        timeout=timeout,
    )


def probe(server_id: str, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Start a server, complete the handshake and list its tools.

    Always stops the child process. Returns a status dict instead of raising.
    """
    out: Dict[str, Any] = {
        "server": server_id, "ok": False, "tools": [],
        "server_info": {}, "error": None, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    client: Optional[MCPClient] = None
    try:
        client = client_for(server_id, timeout=timeout).start()
        out["server_info"] = client.initialize()
        tools = client.list_tools()
        out["tools"] = [
            {
                "name": t.get("name"),
                "description": (t.get("description") or "")[:400],
                "input_schema": t.get("inputSchema") or {},
            }
            for t in tools if isinstance(t, dict)
        ]
        out["ok"] = True
    except Exception as exc:
        out["error"] = str(exc)[:500]
    finally:
        if client is not None:
            out["stderr_tail"] = client.stderr_tail[-8:]
            client.stop()
    return out


def call(server_id: str, tool: str, arguments: Optional[Dict[str, Any]] = None,
         timeout: float = 60.0) -> Dict[str, Any]:
    """One-shot tool call with guaranteed cleanup."""
    client: Optional[MCPClient] = None
    try:
        client = client_for(server_id, timeout=timeout).start()
        client.initialize()
        result = client.call_tool(tool, arguments)
        text = json.dumps(result, default=str)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + "…[truncated]"
        return {"ok": True, "server": server_id, "tool": tool, "result": result,
                "result_preview": text, "error": None}
    except Exception as exc:
        return {"ok": False, "server": server_id, "tool": tool, "result": None,
                "result_preview": "", "error": str(exc)[:500]}
    finally:
        if client is not None:
            client.stop()
