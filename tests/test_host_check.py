"""Every request's Host is checked, against DNS rebinding (issue #31, PRD R8.9, section 10).

A page that makes its own host name resolve to 127.0.0.1 sends requests with *its* name in the Host
header. The tracker answers only to local names (or the ones it was given), so such a page can't read
the private pages or API through the user's browser.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resume_builder.api import create_api
from resume_builder.config import load_settings
from resume_builder.operations import invoke
from resume_builder.space import App
from resume_builder.web.app import create_app

from .conftest import JOB, USER


@pytest.fixture
def api(sample):
    return create_api(sample.app)


def get(api, path, host):
    return TestClient(api, base_url="http://localhost").get(path, headers={"host": host})


def routes(owner_sample):
    """One of each kind of route."""
    return [f"/u/{USER}/", f"/u/{USER}/applications", f"/u/{USER}/jobs/{JOB}", "/docs", "/openapi.json", "/health"]


ALLOWED = ["localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8123", "[::1]", "[::1]:8000", "LOCALHOST:8000"]
FOREIGN = ["evil.example", "evil.example:8000", "localhost.evil.example", "127.0.0.1.nip.io", "attacker.localhost.example:80"]


@pytest.mark.parametrize("host", ALLOWED)
def test_local_names_are_answered(sample, api, host):
    for path in routes(sample):
        assert get(api, path, host).status_code == 200, (path, host)


@pytest.mark.parametrize("host", FOREIGN)
def test_other_names_are_refused_before_any_route(sample, api, host):
    for path in routes(sample):
        r = get(api, path, host)
        assert r.status_code == 400 and "isn't allowed" in r.text, (path, host)


def test_api_reads_and_writes_are_checked_too(sample, owner, api):
    token = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]
    client = TestClient(api, base_url="http://localhost")
    auth = {"authorization": f"Bearer {token}"}
    event = {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "Recruiter replied"}}
    assert client.post(f"/api/{USER}/job_list", json={}).status_code == 200
    assert client.post(f"/api/{USER}/event_propose", json=event, headers=auth).status_code == 200

    drafts = len(sample.repo.event_drafts())
    audits = len(sample.repo.audit_entries(1000))
    evil = {"host": "evil.example"}
    assert client.post(f"/api/{USER}/job_list", json={}, headers=evil).status_code == 400
    assert client.post(f"/api/{USER}/event_propose", json=event, headers={**auth, **evil}).status_code == 400
    assert len(sample.repo.event_drafts()) == drafts              # nothing changed
    assert len(sample.repo.audit_entries(1000)) == audits         # and nothing ran


def test_snapshot_downloads_are_checked(sample, owner, api):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "preview": True, "export": "html"})["snapshot_id"]
    assert get(api, f"/u/{USER}/snapshots/{sid}.html", "localhost").status_code == 200
    assert get(api, f"/u/{USER}/snapshots/{sid}.html", "evil.example").status_code == 400


def test_a_missing_host_is_refused(sample, api):
    assert get(api, "/health", "").status_code == 400


def test_plugin_routes_are_checked(sample):
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/plugin/hello")
    def hello():
        return {"hi": True}

    app_api = create_api(sample.app, routers=[router])
    assert get(app_api, "/plugin/hello", "localhost").status_code == 200
    assert get(app_api, "/plugin/hello", "evil.example").status_code == 400


def test_rb_web_hosts_adds_a_name(sample, api, monkeypatch):
    assert get(api, "/health", "tracker.home.arpa").status_code == 400
    monkeypatch.setenv("RB_WEB_HOSTS", "tracker.home.arpa, other.home.arpa")
    assert get(api, "/health", "tracker.home.arpa:8000").status_code == 200
    assert get(api, "/health", "localhost").status_code == 200   # the local names still work


def test_an_app_can_be_given_its_own_names(roots):
    app = App(load_settings(), web_hosts=["Tracker.Example.Internal"])
    try:
        api = create_api(app)
        assert get(api, "/health", "tracker.example.internal").status_code == 200
        assert get(api, "/health", "localhost").status_code == 400   # only the names it was given
    finally:
        app.close()


def test_create_app_passes_the_names_on(roots):
    web = create_app(settings=load_settings(), web_hosts=["tracker.example.internal"], load_entry_points=False)
    try:
        assert web.state.rb_app.web_hosts == ("tracker.example.internal",)
        assert get(web, "/health", "tracker.example.internal").status_code == 200
    finally:
        web.state.rb_app.close()
