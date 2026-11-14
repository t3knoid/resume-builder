"""MCP server for AI agents (Model Context Protocol, JSON-RPC 2.0 over stdio).

`rb mcp` lets MCP-capable tools (Claude Code, Claude Desktop, Gemini CLI, GitHub Copilot in VS Code,
OpenAI Codex CLI, ...) use resume-builder directly. It always acts as the `agent` actor (B7), and its
tools are generated from the operation registry, filtered to what the agent role may do (A5). So an
agent connected over MCP can propose but never confirm, approve, record events or publish.

Only JSON-RPC messages go to stdout; everything else goes to stderr.
"""

from __future__ import annotations

import copy
import json
import sys
from typing import Any, TextIO

from . import __version__
from . import services, sync_ops  # noqa: F401  (registers operations)
from . import sync as sync_mod
from .errors import AppError
from .operations import REGISTRY, Registry, invoke
from .security import RequestContext
from .services.profiles import agent_instructions
from .space import App

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _deref(schema: dict) -> dict:
    """Inline $ref/$defs: some MCP clients don't resolve references in tool schemas."""
    defs = schema.get("$defs", {})

    def walk(node: Any, seen: tuple = ()) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].split("/")[-1]
                if name in seen:
                    return {"type": "object"}
                target = walk(copy.deepcopy(defs.get(name, {})), seen + (name,))
                extra = {k: walk(v, seen) for k, v in node.items() if k != "$ref"}
                return {**target, **extra}
            return {k: walk(v, seen) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [walk(v, seen) for v in node]
        return node

    out = walk(schema)
    out.setdefault("type", "object")
    out.pop("title", None)
    return out


def tools_for(registry: Registry, role: str = "agent") -> list[dict]:
    """MCP tools: every operation in the registry the role may run (core and plugin operations alike)."""
    tools = []
    for op in registry.for_role(role):
        tools.append({
            "name": op.name,
            "description": f"{op.summary} (permission: {op.permission}{'; saves a draft' if op.writes else ''})",
            "inputSchema": _deref(op.input_model.model_json_schema()),
        })
    return tools


def agent_tools(registry: Registry | None = None) -> list[dict]:
    return tools_for(registry or REGISTRY, "agent")


class McpServer:
    def __init__(self, app: App, user: str, role: str = "agent"):
        self.app = app
        self.user = user
        self.role = role
        self.tools = {t["name"]: t for t in tools_for(app.registry, role)}

    def handle(self, msg: dict) -> dict | None:
        method = msg.get("method")
        mid = msg.get("id")
        if mid is None:  # notification: no response
            return None
        try:
            if method == "initialize":
                requested = (msg.get("params") or {}).get("protocolVersion")
                version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
                result = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "resume-builder", "version": __version__},
                    "instructions": agent_instructions("mcp").replace("<job-id>", "the job id"),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": list(self.tools.values())}
            elif method == "tools/call":
                params = msg.get("params") or {}
                result = self.call(params.get("name", ""), params.get("arguments") or {})
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
        except Exception as exc:  # never crash the session
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(exc)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def call(self, name: str, arguments: dict) -> dict:
        if name not in self.tools:
            return _tool_error(f"Unknown or not permitted tool `{name}`. The agent can only use: {', '.join(self.tools)}.")
        space = self.app.space(self.user)
        with space._lock:
            # Pick up the user's latest file edits before answering (R7.5).
            sync_mod.run_sync(space, RequestContext.system(self.user, tenant_id=self.app.tenant_id))
            if name == "agent_brief":
                arguments = {"mode": "mcp", **arguments}
            try:
                ctx = RequestContext(actor=self.role, roles=(self.role,), user_id=self.user, channel="mcp",
                                     tenant_id=self.app.tenant_id)
                out = invoke(self.app, ctx, name, arguments)
            except AppError as exc:
                lines = [exc.message] + [f"- {p.render()}" for p in exc.problems]
                return _tool_error("\n".join(lines))
        text = out if isinstance(out, str) else json.dumps(out, indent=2, default=str, ensure_ascii=False)
        return {"content": [{"type": "text", "text": text}], "isError": False}


def _tool_error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def serve_stdio(app: App, user: str, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> int:
    server = McpServer(app, user)
    print(f"resume-builder MCP server for `{user}` ready ({len(server.tools)} tools).", file=sys.stderr)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if isinstance(msg, list):
            replies = [r for r in (server.handle(m) for m in msg) if r is not None]
            if replies:
                _write(stdout, replies)
        else:
            reply = server.handle(msg)
            if reply is not None:
                _write(stdout, reply)
    return 0


def _write(stdout: TextIO, obj) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()
