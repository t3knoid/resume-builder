"""MCP over HTTP: local assistants connect with a URL (issue #35).

The same tools and handler as `rb mcp` (stdio), behind the agent's token, the Host check (#31) and an
Origin check. Fictional data only.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from resume_builder.api import MCP_MAX_BYTES, create_api
from resume_builder.mcp import McpServer
from resume_builder.operations import invoke

from .conftest import JOB, USER

URL = f"/mcp/{USER}"


@pytest.fixture
def client(sample):
    return TestClient(create_api(sample.app), base_url="http://localhost")


@pytest.fixture
def token(sample, owner):
    return invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]


def rpc(method, mid=1, **params):
    msg = {"jsonrpc": "2.0", "method": method, "params": params}
    if mid is not None:
        msg["id"] = mid
    return msg


def post(client, token, body, **headers):
    if token:
        headers["authorization"] = f"Bearer {token}"
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return client.post(URL, content=data, headers={"content-type": "application/json", **headers})


# ---------------------------------------------------------------- the same tools as stdio

def test_initialize_list_and_call_match_stdio(sample, client, token):
    r = post(client, token, rpc("initialize", protocolVersion="2025-06-18", clientInfo={"name": "Example Client", "version": "1"}))
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "resume-builder"

    stdio = McpServer(sample.app, USER)
    over_http = post(client, token, rpc("tools/list", mid=2), **{"mcp-protocol-version": "2025-06-18"}).json()["result"]["tools"]
    assert [t["name"] for t in over_http] == [t["name"] for t in stdio.handle(rpc("tools/list", mid=2))["result"]["tools"]]

    call = rpc("tools/call", mid=3, name="job_list", arguments={})
    http_text = post(client, token, call).json()["result"]["content"][0]["text"]
    assert JOB in http_text and http_text == stdio.handle(call)["result"]["content"][0]["text"]


def test_the_agent_still_cannot_use_owner_tools(client, token):
    out = post(client, token, rpc("tools/call", name="job_intake", arguments={"capture_id": 1})).json()["result"]
    assert out["isError"] is True and "not permitted" in out["content"][0]["text"]


def test_calls_are_the_agents_on_the_http_channel(sample, client, token):
    post(client, token, rpc("tools/call", name="event_propose",
                            arguments={"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "x"}}))
    entry = next(e for e in sample.repo.audit_entries() if e["operation"] == "event_propose")
    assert entry["actor"] == "agent" and entry["channel"] == "mcp-http"


# ---------------------------------------------------------------- JSON-RPC over HTTP

def test_notifications_are_accepted_without_a_body(client, token):
    r = post(client, token, rpc("notifications/initialized", mid=None))
    assert r.status_code == 202 and r.content == b""


def test_batches(client, token):
    r = post(client, token, [rpc("ping", mid=7), rpc("notifications/initialized", mid=None), 42])
    replies = r.json()
    assert r.status_code == 200 and len(replies) == 2
    assert replies[0] == {"jsonrpc": "2.0", "id": 7, "result": {}} and replies[1]["error"]["code"] == -32600


def test_bad_json_and_bad_messages(client, token):
    r = post(client, token, b"{not json")
    assert r.status_code == 400 and r.json()["error"]["code"] == -32700
    assert post(client, token, "42").json()["error"]["code"] == -32600


def test_only_post(client, token):
    for method in ("get", "delete"):
        r = getattr(client, method)(URL, headers={"authorization": f"Bearer {token}"})
        assert r.status_code == 405 and r.headers["allow"] == "POST"


def test_protocol_version_header(client, token):
    assert post(client, token, rpc("ping"), **{"mcp-protocol-version": "2025-06-18"}).status_code == 200
    r = post(client, token, rpc("ping"), **{"mcp-protocol-version": "1999-01-01"})
    assert r.status_code == 400 and "Unsupported MCP protocol version" in r.json()["error"]


def test_oversize_requests_are_refused(client, token):
    big = json.dumps(rpc("ping", padding="x" * (MCP_MAX_BYTES + 10)))
    assert post(client, token, big).status_code == 413


# ---------------------------------------------------------------- who may connect

def test_a_token_is_required(sample, client):
    audits = len(sample.repo.audit_entries(1000))
    r = post(client, None, rpc("tools/call", name="job_list", arguments={}))
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer" and "rb token --actor agent" in r.json()["error"]
    r = post(client, "made-up", rpc("ping"))
    assert r.status_code == 401 and "made-up" not in r.text            # the token is never echoed
    assert len(sample.repo.audit_entries(1000)) == audits             # nothing ran


def test_an_owner_token_is_refused(sample, owner, client):
    owner_token = invoke(sample.app, owner, "token_issue", {"actor": "owner"})["token"]
    r = post(client, owner_token, rpc("ping"))
    assert r.status_code == 403 and "agent token" in r.json()["error"]


def test_a_replaced_agent_token_is_refused_and_flagged(sample, owner, client, token):
    invoke(sample.app, owner, "token_issue", {"actor": "agent"})      # replaces `token`
    assert post(client, token, rpc("ping")).status_code == 401
    hints = invoke(sample.app, owner, "agent_status", {})["hints"]
    assert any("no longer valid" in h for h in hints)


def test_foreign_host_and_origin_are_refused(client, token):
    assert post(client, token, rpc("ping"), host="evil.example").status_code == 400          # #31
    r = post(client, token, rpc("ping"), origin="http://evil.example")
    assert r.status_code == 403 and "another website" in r.json()["error"]
    assert post(client, token, rpc("ping"), origin="http://localhost").status_code == 200


def test_unknown_user(client, token):
    r = client.post("/mcp/nobody", content=json.dumps(rpc("ping")),
                    headers={"authorization": f"Bearer {token}", "content-type": "application/json"})
    assert r.status_code == 404


# ---------------------------------------------------------------- the agent status

def test_agent_status_shows_mcp_over_http(sample, owner, client, token):
    post(client, token, rpc("initialize", protocolVersion="2025-06-18", clientInfo={"name": "Example Client", "version": "1"}))
    post(client, token, rpc("tools/call", mid=2, name="agent_brief", arguments={"job_id": JOB}))
    s = invoke(sample.app, owner, "agent_status", {})
    assert s["state"] == "connected" and s["direct_channel"] == "mcp-http"
    assert s["channels"]["mcp-http"]["client"] == "Example Client 1" and s["channels"]["mcp"]["last_seen"] is None
    connects = [e for e in sample.repo.audit_entries() if e["operation"] == "agent:connect"]
    assert connects[0]["channel"] == "mcp-http"
    page = client.get(f"/u/{USER}/").text
    assert "Connected through MCP over HTTP" in page and "Example Client 1" in page
