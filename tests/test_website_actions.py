"""Owner sign-in on the tracker website and its Confirm / Discard buttons (issue #30, PRD 8.8, D32).

Fictional data only; fetching uses a stub.
"""

from __future__ import annotations

import io
import os
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resume_builder.api import create_api
from resume_builder.cli import main
from resume_builder.errors import PermissionDenied
from resume_builder.fetch import FetchedPage
from resume_builder.operations import invoke

from .conftest import JOB, USER

FIXTURES = Path(__file__).parent / "fixtures" / "postings"
URL = "https://careers.example.com/jobs/platform-engineer"
SITE = "http://localhost"
COMPANY = {"name": "company", "value": "Northwind Labs", "passage": "The Platform team at Northwind Labs builds the tools"}


class StubFetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url, limits):
        return FetchedPage(url=url, final_url=url, content_type="text/html", text=self.pages[url],
                           fetched_at="2026-11-15T10:00:00+00:00")


@pytest.fixture
def client(sample):
    sample.app.fetcher = StubFetcher({URL: (FIXTURES / "typical.html").read_text(encoding="utf-8"),
                                      "https://example.com/jobs/1234": (FIXTURES / "heavy-nav.html").read_text(encoding="utf-8")})
    return TestClient(create_api(sample.app), base_url=SITE, follow_redirects=False)


def post(client, path, data=None, origin=SITE, **headers):
    if origin:
        headers["origin"] = origin
    return client.post(path, data=data or {}, headers=headers)


def sign_in(sample, owner, client):
    code = invoke(sample.app, owner, "web_login_link", {})["code"]
    r = post(client, f"/u/{USER}/login", {"code": code})
    assert r.status_code == 303, r.text
    return csrf_of(client)


def csrf_of(client):
    html = client.get(f"/u/{USER}/proposals").text
    m = re.search(r'name="csrf" value="([0-9a-f]{64})"', html)
    assert m, "no CSRF token on the page: not signed in?"
    return m.group(1)


def propose(sample, agent, **kw):
    return invoke(sample.app, agent, "job_intake_propose", kw)["proposal_id"]


# ---------------------------------------------------------------- without signing in: read-only, as before

def test_without_a_session_there_are_no_buttons_and_no_actions(sample, agent, client):
    pid = propose(sample, agent, url=URL)
    html = client.get(f"/u/{USER}/proposals").text
    assert "Confirm and create the job" not in html and f'href="/u/{USER}/login"' in html and "read-only" in html
    for action in ("confirm", "discard"):
        r = post(client, f"/u/{USER}/proposals/{pid}/{action}", {"csrf": "x", "company": "northwind-labs"})
        assert r.status_code == 403
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"


# ---------------------------------------------------------------- signing in

def test_one_time_link_signs_in_once(sample, owner, client):
    code = invoke(sample.app, owner, "web_login_link", {})["code"]
    page = client.get(f"/u/{USER}/login?code={code}")
    assert page.status_code == 200 and f'name="code" value="{code}"' in page.text   # opening the link changes nothing
    r = post(client, f"/u/{USER}/login", {"code": code})
    assert r.status_code == 303 and r.headers["location"] == f"/u/{USER}/"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and f"path=/u/{USER}/" in cookie
    assert "Sign out" in client.get(f"/u/{USER}/").text
    other = TestClient(create_api(sample.app), base_url=SITE, follow_redirects=False)
    assert post(other, f"/u/{USER}/login", {"code": code}).status_code == 403          # used once already


def test_owner_token_signs_in_but_agent_token_does_not(sample, owner, client):
    owner_token = invoke(sample.app, owner, "token_issue", {"actor": "owner"})["token"]
    agent_token = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]
    r = post(client, f"/u/{USER}/login", {"token": agent_token})
    assert r.status_code == 403 and "Agent tokens can't sign in" in r.text
    assert post(client, f"/u/{USER}/login", {"token": "not-a-token"}).status_code == 403
    assert post(client, f"/u/{USER}/login", {"token": owner_token}).status_code == 303
    results = [e["result"] for e in sample.repo.audit_entries() if e["operation"] == "web:sign-in"]
    assert "ok: owner token" in results and results.count("failed: owner token") == 2


def test_only_the_owner_can_make_a_sign_in_link(sample, agent):
    with pytest.raises(PermissionDenied):
        invoke(sample.app, agent, "web_login_link", {})


def test_sign_in_needs_a_local_host_and_the_sites_origin(sample, owner, client):
    code = invoke(sample.app, owner, "web_login_link", {})["code"]
    assert post(client, f"/u/{USER}/login", {"code": code}, origin="http://evil.example").status_code == 403
    evil = TestClient(create_api(sample.app), base_url="http://evil.example", follow_redirects=False)
    assert post(evil, f"/u/{USER}/login", {"code": code}, origin="http://evil.example").status_code == 403
    assert post(client, f"/u/{USER}/login", {"code": code}).status_code == 303          # the code wasn't used up


# ---------------------------------------------------------------- confirm and discard

def test_confirm_creates_exactly_one_job(sample, owner, agent, client):
    pid = propose(sample, agent, url=URL, fields=[COMPANY])
    csrf = sign_in(sample, owner, client)
    html = client.get(f"/u/{USER}/proposals").text
    assert "Confirm and create the job" in html and 'value="northwind-labs" selected' in html
    jobs = set(sample.repo.job_ids())
    r = post(client, f"/u/{USER}/proposals/{pid}/confirm", {"csrf": csrf, "company": "northwind-labs",
                                                             "f_title": "Platform Engineer", "f_location": "Remote, United States"})
    assert r.status_code == 303
    new = set(sample.repo.job_ids()) - jobs
    assert len(new) == 1 and r.headers["location"] == f"/u/{USER}/jobs/{new.pop()}"
    row = sample.repo.get_intake_proposal(pid)
    assert row["status"] == "confirmed" and row["updated_by"] == "owner"
    entry = next(e for e in sample.repo.audit_entries() if e["operation"] == "job_intake_confirm")
    assert entry["actor"] == "owner" and entry["channel"] == "web" and entry["result"] == "ok"


def test_confirm_with_a_new_company_a_changed_field_and_a_new_department(sample, owner, agent, client):
    pid = propose(sample, agent, text=(FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8"))
    csrf = sign_in(sample, owner, client)
    r = post(client, f"/u/{USER}/proposals/{pid}/confirm", {
        "csrf": csrf, "company": "__new__", "new_company": "City of Springfield",
        "department": "", "add_department": "1", "posting_department": "INFORMATION TECHNOLOGY DIVISION",
        "f_title": "Systems Administrator III", "f_salary_range": "", "f_external_id": "714052"})
    assert r.status_code == 303, r.text
    job = r.headers["location"].rsplit("/", 1)[1]
    app = sample.repo.get_application(job)
    assert app.company == "city-of-springfield" and app.department == "information-technology-division"
    assert app.title == "Systems Administrator III"           # changed: your value
    assert app.salary_range is None                           # emptied: cleared
    assert app.external_id == "714052"                         # unchanged: as the posting states


def test_errors_are_shown_next_to_the_form(sample, owner, agent, client):
    pid = propose(sample, agent, url=URL)                      # the posting names no company
    csrf = sign_in(sample, owner, client)
    r = post(client, f"/u/{USER}/proposals/{pid}/confirm", {"csrf": csrf, "company": "", "f_title": "Platform Engineer II"})
    assert r.status_code == 422 and "Not created:" in r.text and "Which company" in r.text
    assert 'value="Platform Engineer II"' in r.text            # what you typed is kept
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"


def test_recapture_from_the_form(sample, owner, agent, client):
    pid = propose(sample, agent, url="https://example.com/jobs/1234")
    csrf = sign_in(sample, owner, client)
    assert f'value="recapture:{JOB}" checked' in client.get(f"/u/{USER}/proposals").text
    r = post(client, f"/u/{USER}/proposals/{pid}/confirm", {"csrf": csrf, "company": "northwind-labs",
                                                             "duplicate": f"recapture:{JOB}", "replace_posting": "1"})
    assert r.status_code == 303 and r.headers["location"] == f"/u/{USER}/jobs/{JOB}"
    assert sample.repo.get_intake_proposal(pid)["job_id"] == JOB


def test_discard(sample, owner, agent, client):
    pid = propose(sample, agent, url=URL)
    csrf = sign_in(sample, owner, client)
    r = post(client, f"/u/{USER}/proposals/{pid}/discard", {"csrf": csrf})
    assert r.status_code == 303 and r.headers["location"] == f"/u/{USER}/proposals?notice=discarded"
    assert "Discarded." in client.get(r.headers["location"]).text
    assert sample.repo.get_intake_proposal(pid)["status"] == "discarded"


def test_proposed_events_get_buttons_too(sample, owner, agent, client):
    d1 = invoke(sample.app, agent, "event_propose", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "Recruiter replied"}})["draft_id"]
    d2 = invoke(sample.app, agent, "event_propose", {"job_id": JOB, "event": {"date": "2026-11-06", "type": "note", "notes": "Spam"}})["draft_id"]
    csrf = sign_in(sample, owner, client)
    assert f'action="/u/{USER}/events/{d1}/confirm"' in client.get(f"/u/{USER}/jobs/{JOB}").text
    before = len(sample.repo.event_rows(JOB))
    assert post(client, f"/u/{USER}/events/{d1}/confirm", {"csrf": csrf, "job_id": JOB}).status_code == 303
    assert post(client, f"/u/{USER}/events/{d2}/discard", {"csrf": csrf, "job_id": JOB}).status_code == 303
    assert len(sample.repo.event_rows(JOB)) == before + 1 and sample.repo.event_drafts(JOB) == []


# ---------------------------------------------------------------- forged requests change nothing

@pytest.mark.parametrize("case", ["no-csrf", "wrong-csrf", "other-origin", "no-origin", "foreign-host"])
def test_forged_requests_are_refused(sample, owner, agent, client, case):
    pid = propose(sample, agent, url=URL, fields=[COMPANY])
    csrf = sign_in(sample, owner, client)
    data = {"csrf": csrf, "company": "northwind-labs"}
    path = f"/u/{USER}/proposals/{pid}/confirm"
    if case == "no-csrf":
        r = post(client, path, {"company": "northwind-labs"})
    elif case == "wrong-csrf":
        r = post(client, path, {**data, "csrf": "0" * 64})
    elif case == "other-origin":
        r = post(client, path, data, origin="http://evil.example")
    elif case == "no-origin":
        r = post(client, path, data, origin=None)
    else:
        r = client.post(path, data=data, headers={"origin": "http://evil.example", "host": "evil.example"})
    assert r.status_code == 403
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"


def test_same_site_referer_is_enough(sample, owner, agent, client):
    pid = propose(sample, agent, url=URL, fields=[COMPANY])
    csrf = sign_in(sample, owner, client)
    r = post(client, f"/u/{USER}/proposals/{pid}/discard", {"csrf": csrf}, origin=None, referer=f"{SITE}/u/{USER}/proposals")
    assert r.status_code == 303


def test_a_get_never_changes_anything(sample, owner, agent, client):
    pid = propose(sample, agent, url=URL, fields=[COMPANY])
    sign_in(sample, owner, client)
    assert client.get(f"/u/{USER}/proposals/{pid}/confirm").status_code in (404, 405)
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"


# ---------------------------------------------------------------- sessions end

def test_sign_out_ends_the_session(sample, owner, agent, client):
    pid = propose(sample, agent, url=URL, fields=[COMPANY])
    csrf = sign_in(sample, owner, client)
    cookie = client.cookies.get("rb_session")
    assert post(client, f"/u/{USER}/logout", {"csrf": csrf}).status_code == 303
    client.cookies.set("rb_session", cookie, path=f"/u/{USER}/")   # even if a copy of the cookie survives
    assert post(client, f"/u/{USER}/proposals/{pid}/discard", {"csrf": csrf}).status_code == 403
    assert "web:sign-out" in {e["operation"] for e in sample.repo.audit_entries()}


@pytest.mark.parametrize("column", ["last_seen_at", "expires_at"])
def test_idle_or_old_sessions_expire(sample, owner, agent, client, column):
    pid = propose(sample, agent, url=URL, fields=[COMPANY])
    csrf = sign_in(sample, owner, client)
    sample.repo.conn.execute(f"UPDATE web_sessions SET {column} = '2026-01-01T00:00:00+00:00'")
    assert post(client, f"/u/{USER}/proposals/{pid}/discard", {"csrf": csrf}).status_code == 403
    assert "Sign in" in client.get(f"/u/{USER}/").text


def test_a_tampered_cookie_is_ignored(sample, owner, client):
    sign_in(sample, owner, client)
    sid, sig = client.cookies.get("rb_session").rsplit(".", 1)
    client.cookies.set("rb_session", f"{sid}.{'0' * len(sig)}", path=f"/u/{USER}/")
    assert "Sign out" not in client.get(f"/u/{USER}/").text


def test_only_hashes_are_stored(sample, owner, client):
    sign_in(sample, owner, client)
    sid = client.cookies.get("rb_session").rsplit(".", 1)[0]
    stored = [r[0] for r in sample.repo.conn.execute("SELECT session_hash FROM web_sessions")]
    assert stored and sid not in stored


def test_the_signing_secret_is_private_and_outside_the_data_folder(sample, owner, client, roots):
    sign_in(sample, owner, client)
    secret = roots / "db" / ".web-secret"
    assert secret.is_file() and not (roots / "data" / ".web-secret").exists()
    if os.name == "posix":
        assert stat.S_IMODE(secret.stat().st_mode) == 0o600


# ---------------------------------------------------------------- headers, escaping, CLI

def test_pages_cannot_be_framed_and_run_no_scripts(client):
    r = client.get(f"/u/{USER}/proposals")
    assert r.headers["x-frame-options"] == "DENY"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "script-src" not in csp and "default-src 'none'" in csp
    assert "content-security-policy" not in client.get("/docs").headers      # the API docs page needs its scripts


def test_hostile_text_is_escaped_inside_the_forms(sample, owner, agent, client):
    hostile = ('"><script>alert(1)</script> Platform Engineer\n\n'
               + "We build internal developer tools and run the CI/CD platform for every team. " * 6)
    propose(sample, agent, text=hostile)
    sign_in(sample, owner, client)
    html = client.get(f"/u/{USER}/proposals").text
    assert "<script>alert(1)" not in html and "&#34;&gt;&lt;script&gt;" in html


def test_rb_web_login_prints_a_link(sample, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setenv("RB_HOST_PORT", "8123")
    assert main(["--user", USER, "web-login"]) == 0
    out = capsys.readouterr().out
    assert re.search(rf"http://localhost:8123/u/{USER}/login\?code=[\w-]+", out)
