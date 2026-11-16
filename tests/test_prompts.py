"""Ready-made assistant prompts: catalog, MCP prompts, the Prompts page and `rb prompts` (issue #37).

Fictional data only.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from resume_builder import prompts as P
from resume_builder.api import create_api
from resume_builder.cli import build_parser, main
from resume_builder.mcp import McpServer, agent_tools
from resume_builder.operations import REGISTRY, invoke

from .conftest import JOB, USER

AGENT_TOOLS = {t["name"] for t in agent_tools()}
ALL_OPS = {op.name for op in REGISTRY.values()}


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z_]+", text))


@pytest.fixture
def client(sample):
    return TestClient(create_api(sample.app), base_url="http://localhost")


# ---------------------------------------------------------------- the catalog

def test_prompts_name_only_tools_the_agent_may_use():
    for p in P.PROMPTS:
        assert set(p.tools) <= AGENT_TOOLS, p.name
        named = words(p.connected) & ALL_OPS
        assert named <= AGENT_TOOLS, (p.name, named - AGENT_TOOLS)      # never confirm, approve, append, publish...
        assert set(p.tools) <= named, (p.name, set(p.tools) - named)       # and every listed tool is in the text


def test_no_prompt_asks_to_invent_set_a_stage_or_confirm():
    for p in P.PROMPTS:
        for body in filter(None, (p.connected, p.paste)):
            low = body.lower()
            assert "item_confirm" not in low and "document_approve" not in low and "event_append" not in low
            assert "estimate" not in low or "never estimate" in low or "don't invent or estimate" in low, p.name


def test_every_command_around_a_prompt_parses():
    values = {"job_id": JOB, "link": "https://jobs.example.com/1"}
    for p in P.PROMPTS:
        for c in P.commands(p.before + p.after + p.instead, values) + P.commands(p.before + p.after + p.instead):
            build_parser().parse_args(list(c.argv))


def test_paste_wording_or_a_command_instead():
    for p in P.PROMPTS:
        assert (p.paste is None) == bool(p.instead), p.name
        if p.before:
            assert "above" in p.paste.lower(), p.name          # it refers to the brief pasted before it


def test_values_are_filled_in_or_left_as_placeholders():
    p = P.BY_NAME["tailor"]
    assert JOB in P.text(p, P.CONNECTED, {"job_id": JOB}) and "<job-id>" in P.text(p, P.CONNECTED)
    with pytest.raises(P.MissingArgument, match="job_id"):
        P.text(p, P.CONNECTED, {}, strict=True)
    assert P.text(P.BY_NAME["add-job"], P.PASTE) is None
    assert "{" not in P.text(P.BY_NAME["notes-to-bullets"], P.CONNECTED, {"notes": "used {braces} here"}).replace("{braces}", "")


def test_preferred_mode():
    assert P.preferred_mode("connected") == P.CONNECTED
    assert {P.preferred_mode(s) for s in ("copy_and_paste", "token_unused", "not_set_up", None)} == {P.PASTE}


# ---------------------------------------------------------------- MCP prompts

def rpc(method, mid=1, **params):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params}


def test_mcp_offers_the_prompts(sample):
    server = McpServer(sample.app, USER)
    init = server.handle(rpc("initialize", protocolVersion="2025-06-18"))["result"]
    assert "prompts" in init["capabilities"]
    listed = server.handle(rpc("prompts/list"))["result"]["prompts"]
    assert [p["name"] for p in listed] == [p.name for p in P.PROMPTS]
    tailor = next(p for p in listed if p["name"] == "tailor")
    assert tailor["arguments"] == [{"name": "job_id", "description": P.JOB.description, "required": True}]


def test_mcp_get_fills_in_the_arguments(sample):
    server = McpServer(sample.app, USER)
    out = server.handle(rpc("prompts/get", name="tailor", arguments={"job_id": JOB}))["result"]
    msg = out["messages"][0]
    assert msg["role"] == "user" and JOB in msg["content"]["text"] and "agent_brief" in msg["content"]["text"]
    missing = server.handle(rpc("prompts/get", name="tailor", arguments={}))["error"]
    assert missing["code"] == -32602 and "job_id" in missing["message"]
    unknown = server.handle(rpc("prompts/get", name="nope"))["error"]
    assert unknown["code"] == -32602 and "tailor" in unknown["message"]


def test_mcp_prompts_over_http(sample, owner, client):
    token = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]
    r = client.post(f"/mcp/{USER}", content=json.dumps(rpc("prompts/get", name="why-here", arguments={"job_id": JOB})),
                    headers={"authorization": f"Bearer {token}", "content-type": "application/json"})
    assert r.status_code == 200 and "slug why-here" in r.json()["result"]["messages"][0]["content"]["text"]


# ---------------------------------------------------------------- website

def test_prompts_page(client):
    r = client.get(f"/u/{USER}/prompts")
    assert r.status_code == 200 and "Copy prompt" in r.text
    assert f"tailor my resume for job {JOB}" in r.text.lower() or JOB in r.text
    assert f"rb brief {JOB}" in r.text                       # no assistant connected yet: copy and paste wording
    connected = client.get(f"/u/{USER}/prompts?mode=connected").text
    assert "agent_brief" in connected and f"rb brief {JOB}" not in connected
    assert "Without one, run:" in client.get(f"/u/{USER}/prompts?mode=paste").text    # add-job, record-email
    assert f'href="/u/{USER}/prompts"' in client.get(f"/u/{USER}/").text
    assert client.get(f"/u/{USER}/prompts?job=2026-01-01-nobody").status_code == 404


def test_the_page_follows_the_agent_status(sample, client):
    McpServer(sample.app, USER).handle(rpc("tools/call", name="job_list", arguments={}))
    page = client.get(f"/u/{USER}/prompts").text
    assert '<option value="connected" selected>' in page


def test_next_steps_show_the_tailor_prompt(sample, owner, client):
    job = invoke(sample.app, owner, "job_new", {"company": "contoso-health", "title": "Site Reliability Engineer"})["job_id"]
    step = next(s for s in invoke(sample.app, owner, "job_next_steps", {"job_id": job}) if s["key"] == "tailor")
    assert step["prompts"] == [{"name": "tailor", "values": {"job_id": job}}]
    page = client.get(f"/u/{USER}/jobs/{job}").text
    assert "Ask your assistant:" in page and f"job {job}" in page


# ---------------------------------------------------------------- rb prompts

def test_rb_prompts(sample, capsys):
    assert main(["--user", USER, "prompts", JOB, "--name", "tailor", "--for", "paste"]) == 0
    out = capsys.readouterr().out
    assert f"rb --user {USER} brief {JOB}" in out and f"| Above is the resume-builder brief for job {JOB}" in out
    assert main(["--user", USER, "prompts", "--for", "connected"]) == 0
    out = capsys.readouterr().out
    assert all(f"[{p.name}]" in out for p in P.PROMPTS) and "<job-id>" in out
    assert main(["--user", USER, "prompts", "--name", "nope"]) != 0
    assert main(["--user", USER, "prompts", "2026-01-01-nobody"]) != 0
