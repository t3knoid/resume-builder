"""Your own answers on a job: passed on by the agent at intake, changed later through an operation (issue #38).

Interest notes, interest level, source, next action and notes are the user's words. The agent may
carry them, never write them; nothing is saved until the owner confirms. Fictional data only;
fetching uses a stub, never the network.
"""

from __future__ import annotations

import io
import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as InputError

from resume_builder.api import create_api
from resume_builder.cli import main
from resume_builder.errors import Conflict, PermissionDenied, ValidationFailed
from resume_builder.fetch import FetchedPage
from resume_builder.mcp import agent_tools
from resume_builder.operations import invoke
from resume_builder.services.profiles import agent_instructions

from .conftest import JOB, USER, data_file

FIXTURES = Path(__file__).parent / "fixtures" / "postings"
URL = "https://careers.example.com/jobs/platform-engineer"
PASTED = (FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8")
COMPANY = {"name": "company", "value": "Northwind Labs", "passage": "The Platform team at Northwind Labs builds the tools"}
NOTES = "I like building the tools other engineers rely on every day."   # not in the posting, on purpose


class StubFetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url, limits):
        return FetchedPage(url=url, final_url=url, content_type="text/html", text=self.pages[url],
                           fetched_at="2026-11-15T10:00:00+00:00")


@pytest.fixture
def site(sample):
    sample.app.fetcher = StubFetcher({URL: (FIXTURES / "typical.html").read_text(encoding="utf-8")})


def propose(sample, agent, **kw):
    return invoke(sample.app, agent, "job_intake_propose", {"url": URL, "fields": [COMPANY], **kw})


ANSWERS = {"interest_level": 4, "interest_notes": NOTES, "source": "referral", "next_action": "Ask Sam about the team",
           "next_action_due": "2026-11-20"}


# ---------------------------------------------------------------- answers in a job proposal

def test_agent_passes_on_answers_and_nothing_is_saved_until_confirmed(sample, owner, agent, site):
    jobs = sample.repo.job_ids()
    out = propose(sample, agent, from_user=ANSWERS)
    assert out["from_user"] == ANSWERS                     # kept as given, not checked against the posting
    assert sample.repo.job_ids() == jobs
    view = invoke(sample.app, owner, "job_intake_proposals", {"proposal_id": out["proposal_id"]})[0]
    assert view["from_user"] == ANSWERS
    made = invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": out["proposal_id"]})
    app = sample.repo.get_application(made["job_id"])
    assert (app.interest_level, app.interest_notes, app.source, app.next_action, str(app.next_action_due)) == \
        (4, NOTES, "referral", "Ask Sam about the team", "2026-11-20")
    assert not any("interest" in step for step in made["next"])    # nothing left to ask for


def test_owner_changes_and_clears_answers_when_confirming(sample, owner, agent, site):
    pid = propose(sample, agent, from_user=ANSWERS)["proposal_id"]
    made = invoke(sample.app, owner, "job_intake_confirm", {
        "proposal_id": pid, "answers": {"interest_notes": "In my words: I build tools for engineers.", "next_action": None},
        "overrides": {"source": "linkedin"}})
    app = sample.repo.get_application(made["job_id"])
    assert app.interest_notes == "In my words: I build tools for engineers."
    assert app.next_action is None and app.interest_level == 4
    assert app.source == "linkedin"                        # your own value for a posting field wins


def test_without_answers_the_job_asks_for_interest_notes(sample, owner, agent, site):
    pid = propose(sample, agent)["proposal_id"]
    assert invoke(sample.app, owner, "job_intake_proposals", {"proposal_id": pid})[0]["from_user"] == {}
    made = invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid})
    assert sample.repo.get_application(made["job_id"]).interest_notes is None
    assert any("rb set" in step and "--interest-notes" in step for step in made["next"])


@pytest.mark.parametrize("bad", [{"interest_level": 7}, {"source": "a friend"}, {"next_action_due": "soon"},
                                 {"stage": "applied"}, {"fit_summary": "Great fit"}])
def test_bad_answers_reject_the_proposal(sample, agent, site, bad):
    with pytest.raises(InputError):
        propose(sample, agent, from_user=bad)
    assert sample.repo.conn.execute("SELECT COUNT(*) FROM intake_proposals").fetchone()[0] == 0


def test_bad_owner_answers_leave_the_proposal_waiting(sample, owner, agent, site):
    pid = propose(sample, agent)["proposal_id"]
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid, "answers": {"interest_level": "9"}})
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"


def test_answers_are_not_applied_to_a_recaptured_job(sample, owner, agent):
    sample.app.fetcher = StubFetcher({"https://example.com/jobs/1234": (FIXTURES / "heavy-nav.html").read_text(encoding="utf-8")})
    before = sample.repo.get_application(JOB).interest_notes
    pid = invoke(sample.app, agent, "job_intake_propose", {"url": "https://example.com/jobs/1234",
                                                            "from_user": {"interest_notes": NOTES}})["proposal_id"]
    out = invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid, "recapture": JOB, "replace_posting": True})
    assert sample.repo.get_application(JOB).interest_notes == before
    assert any(f"rb set {JOB}" in step for step in out["next"])


def test_owner_gives_answers_with_new_job(sample, owner):
    preview = invoke(sample.app, owner, "job_intake_preview", {"text": PASTED})
    made = invoke(sample.app, owner, "job_intake", {"capture_id": preview["capture_id"], "company": "northwind-labs",
                                                     "answers": {"interest_level": "3", "interest_notes": NOTES}})
    app = sample.repo.get_application(made["job_id"])
    assert app.interest_level == 3 and app.interest_notes == NOTES


# ---------------------------------------------------------------- changing them later: the owner

def test_owner_updates_fields_and_the_file_keeps_its_comments(sample, owner, roots):
    stage = sample.repo.application_row(JOB)["stage"]
    events = len(sample.repo.event_rows(JOB))
    out = invoke(sample.app, owner, "application_update", {"job_id": JOB, "changes": {
        "interest_level": 5, "notes": "Ask about on-call.", "next_action": None, "fit_summary": "Strong on CI."}})
    assert out["changed"] == ["fit_summary", "interest_level", "next_action", "notes"] and out["file_updated"]
    app = sample.repo.get_application(JOB)
    assert (app.interest_level, app.notes, app.next_action, app.fit_summary) == (5, "Ask about on-call.", None, "Strong on CI.")
    assert app.interest_notes and app.next_action_due                   # fields not given are left alone
    assert sample.repo.application_row(JOB)["stage"] == stage and len(sample.repo.event_rows(JOB)) == events
    path = data_file(roots, f"jobs/{JOB}/application.yaml")
    path.write_text(path.read_text(encoding="utf-8").replace("notes: Ask about on-call.", "notes: Ask about on-call.   # mine"),
                    encoding="utf-8")
    invoke(sample.app, owner, "sync", {})
    invoke(sample.app, owner, "application_update", {"job_id": JOB, "changes": {"interest_level": 3}})
    text = path.read_text(encoding="utf-8")
    assert "interest_level: 3" in text and "Ask about on-call.   # mine" in text and text.startswith("# yaml-language-server")
    audit = [e for e in sample.repo.audit_entries() if e["operation"] == "application_update"]   # newest first
    assert audit and audit[0]["actor"] == "owner"


@pytest.mark.parametrize("changes", [{}, {"interest_level": 0}, {"stage": "applied"}, {"events": []}, {"title": "x"}])
def test_bad_updates_are_refused(sample, owner, changes):
    with pytest.raises((ValidationFailed, InputError)):
        invoke(sample.app, owner, "application_update", {"job_id": JOB, "changes": changes})


def test_agent_cannot_update_directly(sample, agent):
    for name, payload in [("application_update", {"job_id": JOB, "changes": {"interest_level": 1}}),
                          ("application_draft_confirm", {"draft_id": 1}),
                          ("application_draft_discard", {"draft_id": 1})]:
        with pytest.raises(PermissionDenied):
            invoke(sample.app, agent, name, payload)
    assert sample.repo.get_application(JOB).interest_level == 4


# ---------------------------------------------------------------- changing them later: the agent proposes

def test_agent_proposes_a_change_and_the_owner_confirms(sample, owner, agent):
    out = invoke(sample.app, agent, "application_propose_update",
                 {"job_id": JOB, "changes": {"interest_notes": NOTES, "interest_level": 5}, "note": "From our chat"})
    assert "rb confirm-update" in out["next"]
    assert sample.repo.get_application(JOB).interest_level == 4      # nothing changed yet
    [draft] = invoke(sample.app, owner, "application_drafts", {})
    assert draft["by"] == "agent" and draft["note"] == "From our chat"
    assert {c["field"]: (c["before"], c["after"]) for c in draft["changes"]}["interest_level"] == (4, 5)
    assert invoke(sample.app, owner, "job_get", {"job_id": JOB})["update_drafts"][0]["draft_id"] == draft["draft_id"]
    invoke(sample.app, owner, "application_draft_confirm", {"draft_id": draft["draft_id"]})
    app = sample.repo.get_application(JOB)
    assert app.interest_notes == NOTES and app.interest_level == 5
    assert invoke(sample.app, owner, "application_drafts", {}) == []
    row = sample.repo.get_application_draft(draft["draft_id"])
    assert row["status"] == "confirmed" and row["updated_by"] == "owner"
    with pytest.raises(Conflict):
        invoke(sample.app, owner, "application_draft_confirm", {"draft_id": draft["draft_id"]})


def test_a_draft_is_refused_if_the_field_changed_since(sample, owner, agent):
    did = invoke(sample.app, agent, "application_propose_update", {"job_id": JOB, "changes": {"interest_level": 5}})["draft_id"]
    invoke(sample.app, owner, "application_update", {"job_id": JOB, "changes": {"interest_level": 2}})
    with pytest.raises(Conflict) as e:
        invoke(sample.app, owner, "application_draft_confirm", {"draft_id": did})
    assert "interest_level" in e.value.message
    assert sample.repo.get_application(JOB).interest_level == 2
    assert sample.repo.get_application_draft(did)["status"] == "pending"


def test_discarded_drafts_are_kept_and_never_reopened(sample, owner, agent):
    did = invoke(sample.app, agent, "application_propose_update", {"job_id": JOB, "changes": {"notes": "x"}})["draft_id"]
    assert invoke(sample.app, owner, "application_draft_discard", {"draft_id": did})["status"] == "discarded"
    assert sample.repo.get_application(JOB).notes is None
    with pytest.raises(sqlite3.IntegrityError):
        sample.repo.conn.execute("DELETE FROM application_drafts WHERE id = ?", (did,))
    with pytest.raises(sqlite3.IntegrityError):
        sample.repo.conn.execute("UPDATE application_drafts SET status = 'pending' WHERE id = ?", (did,))


def test_a_proposal_that_changes_nothing_is_refused(sample, agent):
    with pytest.raises(ValidationFailed):
        invoke(sample.app, agent, "application_propose_update", {"job_id": JOB, "changes": {"interest_level": 4}})


def test_status_and_next_steps_show_waiting_changes(sample, owner, agent):
    did = invoke(sample.app, agent, "application_propose_update", {"job_id": JOB, "changes": {"notes": "x"}})["draft_id"]
    assert invoke(sample.app, owner, "space_status", {})["update_drafts_waiting"] == 1
    steps = invoke(sample.app, owner, "job_next_steps", {"job_id": JOB})
    step = next(s for s in steps if s["key"] == "update_drafts")
    assert step["commands"][0]["argv"] == ["confirm-update", str(did)]


# ---------------------------------------------------------------- permissions, tools and instructions

def test_permissions_are_declared(sample):
    reg = sample.app.registry
    assert reg.get("application_update").permission == "application:write"
    assert reg.get("application_propose_update").permission == "application:propose"
    assert reg.get("application_drafts").permission == "application:read"
    assert reg.get("application_draft_confirm").permission == "application:write"
    assert reg.get("application_draft_discard").permission == "application:write"
    for name in ("application_update", "application_propose_update", "application_draft_confirm", "application_draft_discard"):
        assert reg.get(name).writes, name


def test_mcp_offers_the_agent_propose_but_not_apply():
    names = {t["name"] for t in agent_tools()}
    assert {"application_propose_update", "application_drafts"} <= names
    assert not names & {"application_update", "application_draft_confirm", "application_draft_discard"}


def test_agent_is_told_to_pass_on_the_users_words():
    for mode in ("chat", "mcp", "api"):
        text = agent_instructions(mode)
        assert "Never write interest notes yourself" in text and "word for word" in text
    assert "from_user" in agent_instructions("mcp") and "application_propose_update" in agent_instructions("mcp")
    assert "from_user" in agent_instructions("api") and "application_propose_update" in agent_instructions("api")
    assert "rb set <job-id> --interest-notes" in agent_instructions("chat")


# ---------------------------------------------------------------- the CLI

def run(monkeypatch, capsys, argv, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(["--user", USER, *argv])
    return code, capsys.readouterr()


def test_cli_set_and_clear(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["set", JOB, "--interest-level", "2", "--due", "2026-12-01", "--clear", "next_action"])
    assert code == 0, out.err
    app = sample.repo.get_application(JOB)
    assert app.interest_level == 2 and str(app.next_action_due) == "2026-12-01" and app.next_action is None


def test_cli_set_reads_long_text_from_stdin(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["set", JOB, "--interest-notes", "-"], stdin="﻿" + NOTES + "\n")
    assert code == 0, out.err
    assert sample.repo.get_application(JOB).interest_notes == NOTES


def test_cli_set_needs_a_field(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["set", JOB])
    assert code == 1 and "Nothing to change" in out.err


def test_cli_update_drafts_confirm_and_discard(sample, agent, monkeypatch, capsys):
    first = invoke(sample.app, agent, "application_propose_update", {"job_id": JOB, "changes": {"interest_level": 5}})["draft_id"]
    second = invoke(sample.app, agent, "application_propose_update", {"job_id": JOB, "changes": {"notes": "x"}})["draft_id"]
    code, out = run(monkeypatch, capsys, ["update-drafts"])
    assert code == 0 and f"#{first}" in out.out and "interest_level: 4  ->  5" in out.out and "rb confirm-update" in out.out
    assert run(monkeypatch, capsys, ["confirm-update", str(first)])[0] == 0
    assert run(monkeypatch, capsys, ["discard-update", str(second)])[0] == 0
    assert sample.repo.get_application(JOB).interest_level == 5 and sample.repo.get_application(JOB).notes is None
    assert "No changes waiting" in run(monkeypatch, capsys, ["update-drafts"])[1].out


def test_cli_confirm_job_shows_and_changes_the_answers(sample, agent, site, monkeypatch, capsys):
    pid = propose(sample, agent, from_user={"interest_notes": NOTES, "interest_level": 4})["proposal_id"]
    code, out = run(monkeypatch, capsys, ["confirm-job", str(pid)])
    assert code == 0 and "check they are your words" in out.out and NOTES in out.out and "from you, via the agent" in out.out
    code, out = run(monkeypatch, capsys, ["confirm-job", str(pid), "--set", "interest_level=5", "--yes"])
    assert code == 0, out.err
    job = next(j for j in sample.repo.job_ids() if j.endswith("-northwind-labs"))
    app = sample.repo.get_application(job)
    assert app.interest_level == 5 and app.interest_notes == NOTES


def test_cli_new_job_takes_your_answers(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["new-job", "--stdin", "--company", "northwind-labs", "--yes",
                                          "--set", f"interest_notes={NOTES}", "--set", "interest_level=3"], PASTED)
    assert code == 0, out.err
    job = re.search(r"Created (\S+)", out.out).group(1)
    assert sample.repo.get_application(job).interest_notes == NOTES


# ---------------------------------------------------------------- the website

SITE = "http://localhost"


@pytest.fixture
def client(sample):
    sample.app.fetcher = StubFetcher({URL: (FIXTURES / "typical.html").read_text(encoding="utf-8")})
    return TestClient(create_api(sample.app), base_url=SITE, follow_redirects=False)


def post(client, path, data):
    return client.post(path, data=data, headers={"origin": SITE})


def sign_in(sample, owner, client) -> str:
    code = invoke(sample.app, owner, "web_login_link", {})["code"]
    assert post(client, f"/u/{USER}/login", {"code": code}).status_code == 303
    html = client.get(f"/u/{USER}/jobs/{JOB}").text
    return re.search(r'name="csrf" value="([0-9a-f]{64})"', html).group(1)


def test_website_details_need_a_session(sample, client):
    html = client.get(f"/u/{USER}/jobs/{JOB}").text
    assert "Save details" not in html and f"rb set {JOB}" in html
    assert post(client, f"/u/{USER}/jobs/{JOB}/details", {"csrf": "x", "d_interest_level": "1"}).status_code == 403
    assert sample.repo.get_application(JOB).interest_level == 4


def test_website_saves_only_changed_details(sample, owner, client):
    csrf = sign_in(sample, owner, client)
    app = sample.repo.get_application(JOB)
    r = post(client, f"/u/{USER}/jobs/{JOB}/details", {
        "csrf": csrf, "d_interest_level": "5", "d_interest_notes": app.interest_notes, "d_next_action": "",
        "d_source": app.source, "d_notes": ""})
    assert r.status_code == 303, r.text
    after = sample.repo.get_application(JOB)
    assert after.interest_level == 5 and after.next_action is None and after.interest_notes == app.interest_notes
    audit = [e for e in sample.repo.audit_entries() if e["operation"] == "application_update"]
    assert audit[0]["channel"] == "web"


def test_website_confirms_an_agent_change(sample, owner, agent, client):
    did = invoke(sample.app, agent, "application_propose_update", {"job_id": JOB, "changes": {"interest_level": 5}})["draft_id"]
    csrf = sign_in(sample, owner, client)
    html = client.get(f"/u/{USER}/jobs/{JOB}").text
    assert f"/u/{USER}/updates/{did}/confirm" in html
    assert post(client, f"/u/{USER}/updates/{did}/confirm", {"csrf": csrf, "job_id": JOB}).status_code == 303
    assert sample.repo.get_application(JOB).interest_level == 5


def test_website_proposal_shows_and_changes_the_answers(sample, owner, agent, client):
    pid = propose(sample, agent, from_user={"interest_notes": NOTES, "source": "referral"})["proposal_id"]
    csrf = sign_in(sample, owner, client)
    html = client.get(f"/u/{USER}/proposals").text
    assert "check these are your words" in html and NOTES in html
    r = post(client, f"/u/{USER}/proposals/{pid}/confirm", {
        "csrf": csrf, "company": "northwind-labs", "f_source": "referral", "a_interest_notes": NOTES,
        "a_interest_level": "4"})
    assert r.status_code == 303, r.text
    app = sample.repo.get_application(r.headers["location"].rsplit("/", 1)[1])
    assert app.interest_notes == NOTES and app.interest_level == 4 and app.source == "referral"
