"""Agent integrations: MCP server, pasted drafts, standing instructions (docs/AI-AGENTS.md)."""

import io
import json

from resume_builder.mcp import McpServer, agent_tools, serve_stdio
from resume_builder.operations import REGISTRY, invoke
from resume_builder.security import ROLE_PERMISSIONS
from resume_builder.services.profiles import agent_instructions

from .conftest import JOB, USER


def _session(sample, *messages):
    out = io.StringIO()
    serve_stdio(sample.app, USER, io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n"), out)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_mcp_handshake_and_tools(sample):
    replies = _session(sample,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "nope"})
    assert [r["id"] for r in replies] == [1, 2, 3]  # no reply to the notification
    init = replies[0]["result"]
    assert init["protocolVersion"] == "2025-06-18" and "Never invent" in init["instructions"]
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert {"agent_brief", "profile_write", "document_propose", "event_propose"} <= names
    assert replies[2]["error"]["code"] == -32601


def test_mcp_exposes_only_agent_permissions():
    allowed = ROLE_PERMISSIONS["agent"]
    names = {t["name"] for t in agent_tools()}
    for op in REGISTRY.values():
        assert (op.name in names) == (op.permission in allowed), op.name
    assert not {"document_approve", "item_confirm", "library_confirm_all", "event_append", "sync", "publish_public"} & names
    for t in agent_tools():
        assert "$ref" not in json.dumps(t["inputSchema"]), t["name"]  # schemas inlined for picky clients


def test_mcp_call_acts_as_agent_and_returns_problems(sample, owner):
    server = McpServer(sample.app, USER)
    ok = server.call("agent_brief", {"job_id": JOB})
    assert not ok["isError"] and "profile_write" in ok["content"][0]["text"]  # MCP-mode brief
    bad = server.call("document_propose", {"job_id": JOB, "kind": "answer", "text": "I ran Kubernetes for 9 years."})
    assert bad["isError"] and "citation" in bad["content"][0]["text"]
    refused = server.call("document_approve", {"job_id": JOB, "kind": "answer"})
    assert refused["isError"]
    server.call("event_propose", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "Recruiter replied"}})
    entries = invoke(sample.app, owner, "audit_list", {})
    assert any(e["actor"] == "agent" and e["channel"] == "mcp" for e in entries)


def test_pasted_profile_in_code_fence(sample, agent):
    text = "```yaml\nsummary: [sum-platform]\nroles:\n  role-fabrikam: [fab-ci-pipeline]\n  role-tailspin: [tail-docker]\n```\n"
    out = invoke(sample.app, agent, "profile_propose_text", {"key": JOB, "text": text})
    assert out["saved"] == JOB
    assert sample.repo.get_profile(JOB).roles["role-fabrikam"] == ["fab-ci-pipeline"]


def test_rb_propose_reads_piped_text_with_a_byte_order_mark(sample, monkeypatch, capsys):
    """Windows PowerShell 5.1 pipes text with a BOM in front; `rb propose` must ignore it."""
    import io

    from resume_builder.cli import main

    text = "﻿```yaml\nsummary: [sum-platform]\nroles:\n  role-fabrikam: [fab-ci-pipeline]\n  role-tailspin: [tail-docker]\n```\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert main(["--user", "alex-morgan", "propose", JOB, "profile"]) == 0
    assert '"saved"' in capsys.readouterr().out
    assert sample.repo.get_profile(JOB).roles["role-fabrikam"] == ["fab-ci-pipeline"]


def test_pasted_profile_errors_are_explained(sample, agent):
    import pytest
    from resume_builder.errors import ValidationFailed
    with pytest.raises(ValidationFailed) as e:
        invoke(sample.app, agent, "profile_propose_text", {"key": JOB, "text": "summary: [sum-platform]\nrole: {}\n"})
    assert any("not a known field" in p.message for p in e.value.problems)


def test_instructions_cover_every_mode():
    for mode in ("chat", "mcp", "api"):
        text = agent_instructions(mode)
        assert "Never invent" in text and "[cite:" in text
    assert "rb propose" in agent_instructions("chat")
