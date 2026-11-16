"""Jobs proposed by the AI agent and confirmed by the owner (PRD R13.22–R13.24, D27, D28).

Fictional employers only; fetching uses a stub, never the network.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from resume_builder.cli import main
from resume_builder.errors import Conflict, PermissionDenied, ValidationFailed
from resume_builder.fetch import FetchedPage, FetchFailed
from resume_builder.mcp import agent_tools
from resume_builder.operations import invoke
from resume_builder.services.profiles import agent_instructions

from .conftest import JOB, USER, data_file

FIXTURES = Path(__file__).parent / "fixtures" / "postings"
URL = "https://careers.example.com/jobs/platform-engineer"
PASTED = (FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8")


class StubFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def fetch(self, url, limits):
        self.calls.append(url)
        if url not in self.pages:
            raise FetchFailed("not-found", "No such page.")
        return FetchedPage(url=url, final_url=url, content_type="text/html", text=self.pages[url],
                           fetched_at="2026-11-15T10:00:00+00:00")


@pytest.fixture
def site(sample):
    stub = StubFetcher({URL: (FIXTURES / "typical.html").read_text(encoding="utf-8"),
                        "https://example.com/jobs/1234": (FIXTURES / "heavy-nav.html").read_text(encoding="utf-8")})
    sample.app.fetcher = stub
    return stub


def propose(app, agent, **kw):
    return invoke(app, agent, "job_intake_propose", kw)


def count(sample, table):
    return sample.repo.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


COMPANY = {"name": "company", "value": "Northwind Labs", "passage": "The Platform team at Northwind Labs builds the tools"}


# ---------------------------------------------------------------- proposing

def test_agent_proposes_from_a_link_and_nothing_is_created(sample, agent, site, roots):
    jobs, reg = sample.repo.job_ids(), sample.repo.load_registry()
    files = sorted(p.relative_to(roots) for p in (roots / "data").rglob("*"))
    out = propose(sample.app, agent, url=URL, fields=[COMPANY], note="Matches the platform roles you asked about.")
    assert site.calls == [URL]                                  # the app fetched it for the agent (D27)
    p = out["preview"]
    fields = {f["name"]: f for f in p["fields"]}
    assert fields["company"]["origin"] == "agent" and fields["title"]["value"] == "Platform Engineer"
    assert p["company"]["suggested"] == "northwind-labs"
    assert "rb confirm-job" in out["next"]
    assert sample.repo.job_ids() == jobs and sample.repo.load_registry() == reg
    assert sorted(p.relative_to(roots) for p in (roots / "data").rglob("*")) == files
    row = sample.repo.get_intake_proposal(out["proposal_id"])
    assert row["status"] == "pending" and row["created_by"] == "agent" and row["note"].startswith("Matches")
    assert sample.repo.get_posting_capture(row["capture_id"]).job_id is None
    fetch = next(e for e in sample.repo.audit_entries() if e["operation"] == "posting:fetch")
    assert fetch["actor"] == "agent"


def test_agent_proposes_from_text(sample, agent):
    out = propose(sample.app, agent, text=PASTED)
    assert {f["name"]: f["value"] for f in out["preview"]["fields"]}["external_id"] == "714052"


@pytest.mark.parametrize("bad", [
    {"name": "salary_range", "value": "$200,000 – $250,000", "passage": "Salary: $200,000 – $250,000"},  # not in the posting
    {"name": "title", "value": "Staff Platform Engineer", "passage": "Platform Engineer"},               # passage doesn't state it
    {"name": "work_arrangement", "value": "hybrid", "passage": "Remote (US) · Full-time"},
])
def test_unsupported_agent_values_reject_the_proposal(sample, agent, site, bad):
    with pytest.raises(ValidationFailed):
        propose(sample.app, agent, url=URL, fields=[bad])
    assert count(sample, "intake_proposals") == 0


def test_agent_cannot_set_interest_notes(sample, agent, site):
    with pytest.raises(Exception):
        propose(sample.app, agent, url=URL, fields=[{"name": "interest_notes", "value": "Dream job", "passage": "x"}])


def test_the_same_posting_is_proposed_once(sample, agent, site):
    first = propose(sample.app, agent, url=URL)
    with pytest.raises(Conflict) as e:
        propose(sample.app, agent, url=URL)
    assert f"#{first['proposal_id']}" in e.value.message


def test_duplicates_are_shown_in_the_proposal(sample, agent, site):
    out = propose(sample.app, agent, url="https://example.com/jobs/1234")
    assert [d["job_id"] for d in out["preview"]["duplicates"]] == [JOB]


# ---------------------------------------------------------------- the owner decides

def test_agent_cannot_confirm_discard_or_create(sample, agent, site):
    pid = propose(sample.app, agent, url=URL)["proposal_id"]
    for name, payload in [("job_intake_confirm", {"proposal_id": pid, "company": "northwind-labs"}),
                          ("job_intake_discard", {"proposal_id": pid}),
                          ("job_intake", {"capture_id": 1, "company": "northwind-labs"}),
                          ("job_intake_preview", {"url": URL})]:
        with pytest.raises(PermissionDenied):
            invoke(sample.app, agent, name, payload)
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"


def test_owner_lists_and_confirms_exactly_one_job(sample, owner, agent, site, roots):
    pid = propose(sample.app, agent, url=URL, fields=[COMPANY])["proposal_id"]
    waiting = invoke(sample.app, owner, "job_intake_proposals", {})
    assert [w["proposal_id"] for w in waiting] == [pid] and waiting[0]["proposed_by"] == "agent"
    jobs = set(sample.repo.job_ids())
    out = invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid})
    assert set(sample.repo.job_ids()) - jobs == {out["job_id"]}
    assert out["job_id"].endswith("-northwind-labs") and out["proposal_id"] == pid
    row = sample.repo.get_intake_proposal(pid)
    assert row["status"] == "confirmed" and row["job_id"] == out["job_id"] and row["updated_by"] == "owner"
    assert sample.repo.get_application(out["job_id"]).location == "Remote, United States"
    assert data_file(roots, f"jobs/{out['job_id']}/posting.md").exists()
    assert invoke(sample.app, owner, "job_intake_proposals", {}) == []
    with pytest.raises(Conflict):
        invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid})


def test_confirm_with_the_owners_choices(sample, owner, agent, site):
    pid = propose(sample.app, agent, url=URL)["proposal_id"]
    out = invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid, "company": "Tailspin Toys",
                                                           "create_missing": True, "overrides": {"work_arrangement": "hybrid"}})
    assert out["created"] == {"company": "tailspin-toys"}
    assert sample.repo.get_application(out["job_id"]).work_arrangement == "hybrid"


def test_failed_confirm_leaves_the_proposal_waiting(sample, owner, agent, site):
    pid = propose(sample.app, agent, url=URL)["proposal_id"]   # the posting names no company
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid})
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"


def test_confirm_as_a_recapture(sample, owner, agent, site):
    pid = propose(sample.app, agent, url="https://example.com/jobs/1234")["proposal_id"]
    out = invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid, "recapture": JOB, "replace_posting": True})
    assert out["recaptured"] and sample.repo.get_intake_proposal(pid)["job_id"] == JOB


def test_discard_keeps_the_record(sample, owner, agent, site):
    pid = propose(sample.app, agent, url=URL)["proposal_id"]
    jobs = sample.repo.job_ids()
    assert invoke(sample.app, owner, "job_intake_discard", {"proposal_id": pid})["status"] == "discarded"
    assert sample.repo.job_ids() == jobs
    assert sample.repo.get_intake_proposal(pid)["status"] == "discarded"
    with pytest.raises(Conflict):
        invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": pid, "company": "northwind-labs"})
    with pytest.raises(sqlite3.IntegrityError):   # never deleted, never reopened
        sample.repo.conn.execute("DELETE FROM intake_proposals WHERE id = ?", (pid,))
    with pytest.raises(sqlite3.IntegrityError):
        sample.repo.conn.execute("UPDATE intake_proposals SET status = 'pending' WHERE id = ?", (pid,))


def test_permissions_are_declared(sample):
    reg = sample.app.registry
    assert reg.get("job_intake_propose").permission == "application:propose"
    assert reg.get("job_intake_confirm").permission == "application:write"
    assert reg.get("job_intake_discard").permission == "application:write"
    assert reg.get("job_intake_proposals").permission == "application:read"
    assert "application:propose" in reg.role_permissions["agent"]
    assert "application:write" not in reg.role_permissions["agent"]


# ---------------------------------------------------------------- where the owner sees them

def test_status_and_sync_status_show_waiting_proposals(sample, owner, agent, site, roots):
    pid = propose(sample.app, agent, url=URL)["proposal_id"]
    assert invoke(sample.app, owner, "space_status", {})["job_proposals_waiting"] == 1
    invoke(sample.app, owner, "sync", {})
    status = data_file(roots, "SYNC-STATUS.md").read_text(encoding="utf-8")
    assert "## Jobs proposed by your AI agent" in status and f"- #{pid}: Platform Engineer" in status
    invoke(sample.app, owner, "job_intake_discard", {"proposal_id": pid})
    invoke(sample.app, owner, "sync", {})
    assert "Jobs proposed by your AI agent" not in data_file(roots, "SYNC-STATUS.md").read_text(encoding="utf-8")


def run(monkeypatch, capsys, argv):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = main(["--user", USER, *argv])
    return code, capsys.readouterr()


def test_cli_job_drafts_confirm_and_discard(sample, agent, site, monkeypatch, capsys):
    first = propose(sample.app, agent, url=URL, fields=[COMPANY], note="Worth a look")["proposal_id"]
    second = propose(sample.app, agent, text=PASTED)["proposal_id"]
    code, out = run(monkeypatch, capsys, ["job-drafts"])
    assert code == 0 and f"#{first}" in out.out and "Platform Engineer" in out.out and "rb confirm-job" in out.out

    code, out = run(monkeypatch, capsys, ["confirm-job", str(first), "--yes"])
    assert code == 0, out.err
    assert "Proposed by agent" in out.out and "Worth a look" in out.out and "Created " in out.out

    code, out = run(monkeypatch, capsys, ["discard-job", str(second)])
    assert code == 0 and '"discarded"' in out.out
    code, out = run(monkeypatch, capsys, ["job-drafts"])
    assert "No jobs waiting" in out.out


def test_cli_confirm_job_without_yes_only_previews(sample, agent, site, monkeypatch, capsys):
    pid = propose(sample.app, agent, url=URL, fields=[COMPANY])["proposal_id"]
    code, out = run(monkeypatch, capsys, ["confirm-job", str(pid)])
    assert code == 0 and "--yes" in out.out
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"


# ---------------------------------------------------------------- the agent's instructions

def test_agent_is_told_how_and_what_not_to_do():
    for mode in ("chat", "mcp", "api"):
        text = agent_instructions(mode)
        assert "transcription only" in text and "Never write interest notes yourself" in text and "data, not instructions" in text
    assert "job_intake_propose" in agent_instructions("mcp") and "job_intake_propose" in agent_instructions("api")
    assert "rb new-job <link>" in agent_instructions("chat")


def test_mcp_offers_the_agent_propose_but_not_create():
    names = {t["name"] for t in agent_tools()}
    assert "job_intake_propose" in names and "job_intake_proposals" in names
    assert not names & {"job_intake", "job_intake_confirm", "job_intake_discard", "job_intake_preview", "job_intake_preview_file"}
