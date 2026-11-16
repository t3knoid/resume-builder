"""Whether an AI agent is set up and when it was last active (issue #32).

The app sees only what reaches it: operations the agent runs per channel, MCP connections, and the
agent token. Fictional data only.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from resume_builder.api import create_api
from resume_builder.cli import main
from resume_builder.mcp import McpServer
from resume_builder.operations import invoke
from resume_builder.security import RequestContext
from resume_builder.services.agent_status import clean_client, note_seen

from .conftest import JOB, USER


@pytest.fixture
def client(sample):
    return TestClient(create_api(sample.app), base_url="http://localhost")


def status(sample, owner):
    return invoke(sample.app, owner, "agent_status", {})


def home(client):
    return client.get(f"/u/{USER}/").text


def initialize(server, client_info=None, mid=1):
    params = {"protocolVersion": "2025-06-18"}
    if client_info is not None:
        params["clientInfo"] = client_info
    return server.handle({"jsonrpc": "2.0", "id": mid, "method": "initialize", "params": params})


def connects(sample):
    return [e for e in sample.repo.audit_entries(1000) if e["operation"] == "agent:connect"]


# ---------------------------------------------------------------- the three states

def test_not_set_up(sample, owner, client):
    s = status(sample, owner)
    assert s["state"] == "not_set_up" and s["last_active"] is None
    assert any("No AI agent has connected yet" in h for h in s["hints"])
    assert home(client).count("No AI agent has connected yet.") == 1      # stated once, not twice


def test_token_issued_but_not_used(sample, owner, client):
    token = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]
    s = status(sample, owner)
    assert s["state"] == "token_unused" and s["channels"]["api"]["token_used"] is False
    page = home(client)
    assert page.count("been used yet") == 1                   # the page escapes the apostrophe
    assert f"issued on {s['channels']['api']['token_issued'][:10]}" in page
    for text in (json.dumps(s), page):
        assert token not in text                      # the token itself is never shown


def test_api_use_marks_the_agent_connected(sample, owner, client):
    token = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]
    r = client.post(f"/api/{USER}/agent_brief", json={"job_id": JOB, "mode": "api"},
                    headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200                           # a read: not audited, but it counts as activity
    s = status(sample, owner)
    assert s["state"] == "connected" and s["last_channel"] == "api"
    assert s["channels"]["api"]["token_used"] is True and s["channels"]["mcp"]["last_seen"] is None
    assert "Connected through the HTTP API" in home(client)


def test_mcp_connection_and_tool_calls(sample, owner, client):
    server = McpServer(sample.app, USER)
    initialize(server, {"name": "Example Client", "version": "2.1"})
    initialize(server, {"name": "Example Client", "version": "2.1"}, mid=2)
    assert len(connects(sample)) == 1 and connects(sample)[0]["target"] == "Example Client 2.1"   # once an hour at most
    out = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "agent_brief", "arguments": {"job_id": JOB}}})
    assert out["result"]["isError"] is False
    s = status(sample, owner)
    assert s["state"] == "connected" and s["last_channel"] == "mcp"
    assert s["channels"]["mcp"]["client"] == "Example Client 2.1"
    assert "Connected through MCP" in home(client) and "Example Client 2.1" in home(client)


def test_copy_and_paste_alone_is_not_connected(sample, owner, client, monkeypatch, capsys):
    """Drafts pasted with rb propose arrive as the agent's work, but no assistant is wired up (#33)."""
    invoke(sample.app, RequestContext.agent(USER, channel="cli"), "job_list", {})
    s = status(sample, owner)
    assert s["state"] == "copy_and_paste" and s["last_channel"] == "cli" and s["channels"]["cli"]["last_seen"]
    assert s["direct_channel"] is None
    assert any("no assistant has connected directly" in h for h in s["hints"])
    page = home(client)
    assert page.count("Used through copy and paste") == 1 and "Connected through" not in page
    assert page.count("no assistant has connected directly") == 1
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["--user", USER, "status"]) == 0
    assert '"state": "copy_and_paste"' in capsys.readouterr().out


def test_copy_and_paste_after_a_direct_connection_is_still_connected(sample, owner, client):
    McpServer(sample.app, USER).handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                        "params": {"name": "agent_brief", "arguments": {"job_id": JOB}}})
    sample.repo.meta_set("agent.last_seen.mcp", "2026-11-14T09:00:00+00:00")   # an earlier MCP session
    invoke(sample.app, RequestContext.agent(USER, channel="cli"), "job_list", {})  # then a pasted draft
    s = status(sample, owner)
    assert s["state"] == "connected" and s["last_channel"] == "cli"
    assert s["direct_channel"] == "mcp" and s["direct_active"] == "2026-11-14T09:00:00+00:00"
    page = home(client)
    assert "Connected through MCP, last on 2026-11-14T09:00:00+00:00." in page and "Most recently used through copy and paste" in page


def test_copy_and_paste_with_an_unused_token_points_to_the_token(sample, owner):
    invoke(sample.app, RequestContext.agent(USER, channel="cli"), "job_list", {})
    invoke(sample.app, owner, "token_issue", {"actor": "agent"})
    assert status(sample, owner)["state"] == "token_unused"


# ---------------------------------------------------------------- a replaced token

def test_a_replaced_token_is_flagged_until_the_new_one_is_used(sample, owner, client):
    old = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]
    new = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]       # replaces the old one
    for _ in range(3):
        r = client.post(f"/api/{USER}/job_list", json={}, headers={"authorization": f"Bearer {old}"})
        assert r.status_code == 403                                                      # still refused
    s = status(sample, owner)
    assert any("no longer valid" in h for h in s["hints"])
    assert len([e for e in sample.repo.audit_entries(1000) if e["operation"] == "api:token-replaced"]) == 1
    assert "no longer valid" in home(client)
    assert client.post(f"/api/{USER}/job_list", json={}, headers={"authorization": f"Bearer {new}"}).status_code == 200
    assert not any("no longer valid" in h for h in status(sample, owner)["hints"])


def test_an_unknown_token_is_not_mistaken_for_a_replaced_one(sample, owner, client):
    client.post(f"/api/{USER}/job_list", json={}, headers={"authorization": "Bearer made-up"})
    assert not status(sample, owner)["hints"][1:]   # only the "not set up" hint


# ---------------------------------------------------------------- what the agent did, and what's waiting

def test_recent_actions_and_waiting_items(sample, owner, agent, client):
    invoke(sample.app, agent, "event_propose", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "x"}})
    McpServer(sample.app, USER).handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    s = status(sample, owner)
    assert s["recent"][0]["operation"] == "event_propose" and s["recent"][0]["job_id"] == JOB
    assert all(r["operation"] != "agent:connect" for r in s["recent"])
    assert s["waiting"] == {"jobs": 0, "events": 1, "updates": 0}
    page = home(client)
    assert f'<a href="/u/{USER}/jobs/{JOB}">{JOB}</a>' in page and "1 proposed event(s)" in page


def test_space_status_and_rb_status_include_the_agent(sample, owner, monkeypatch, capsys):
    assert invoke(sample.app, owner, "space_status", {})["agent"]["state"] == "not_set_up"
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["--user", USER, "status"]) == 0
    assert '"agent"' in capsys.readouterr().out


# ---------------------------------------------------------------- bookkeeping is cheap, safe and escaped

def test_last_seen_is_written_at_most_once_a_minute(sample):
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=30)).replace(microsecond=0).isoformat()
    sample.repo.meta_set("agent.last_seen.api", stamp)
    note_seen(sample, "api")
    assert sample.repo.meta_get("agent.last_seen.api") == stamp
    old = (datetime.now(timezone.utc) - timedelta(minutes=2)).replace(microsecond=0).isoformat()
    sample.repo.meta_set("agent.last_seen.api", old)
    note_seen(sample, "api")
    assert sample.repo.meta_get("agent.last_seen.api") > old


def test_bookkeeping_never_breaks_the_agents_call(sample, agent, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("meta table locked")

    monkeypatch.setattr(sample.repo, "meta_set", boom)
    assert invoke(sample.app, agent, "job_list", {}) is not None


def test_client_names_are_cleaned_and_escaped(sample, owner, client):
    assert clean_client("Tool\x00\nName", "1") == "Tool Name 1" and clean_client(None) is None
    assert len(clean_client("x" * 500)) == 80
    initialize(McpServer(sample.app, USER), {"name": "<script>alert(1)</script>", "version": {"not": "text"}})
    page = home(client)
    assert "<script>alert(1)" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_permissions(sample, agent):
    assert sample.app.registry.get("agent_status").permission == "report:read"
    assert invoke(sample.app, agent, "agent_status", {})["state"] == "connected"   # the agent can see its own status
    viewer = RequestContext(actor="viewer", roles=("viewer",), user_id=USER)
    assert invoke(sample.app, viewer, "agent_status", {})["state"]


def test_agent_work_from_before_this_feature_counts(sample, owner):
    """An agent that proposed something before "last seen" existed shows as connected, not "not set up"."""
    sample.repo.audit("agent", "mcp", "req-1", "event_propose", JOB, "ok")
    s = status(sample, owner)
    assert s["state"] == "connected" and s["last_channel"] == "mcp" and s["channels"]["mcp"]["last_seen"]
