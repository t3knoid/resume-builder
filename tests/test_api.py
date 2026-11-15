"""Generated HTTP API, token identity and the website (PRD A5, B9a, 8.8)."""

from fastapi.testclient import TestClient

from resume_builder.api import create_api
from resume_builder.operations import REGISTRY, invoke

from .conftest import JOB, USER


def _client(sample):
    return TestClient(create_api(sample.app), base_url="http://localhost")


def test_every_operation_has_a_route(sample):
    spec = _client(sample).get("/openapi.json").json()
    for name in REGISTRY:
        assert f"/api/{{user}}/{name}" in spec["paths"], name


def test_anonymous_can_read_but_not_write(sample):
    c = _client(sample)
    assert c.post(f"/api/{USER}/job_list", json={}).status_code == 200
    r = c.post(f"/api/{USER}/event_append", json={"job_id": JOB, "event": {"date": "2026-11-05", "type": "note"}})
    assert r.status_code == 403


def test_agent_token_can_propose_not_approve(sample, owner):
    token = invoke(sample.app, owner, "token_issue", {"actor": "agent"})["token"]
    c = _client(sample)
    h = {"Authorization": f"Bearer {token}"}
    r = c.post(f"/api/{USER}/event_propose", json={"job_id": JOB, "event": {"date": "2026-11-05", "type": "note"}}, headers=h)
    assert r.status_code == 200, r.text
    r = c.post(f"/api/{USER}/document_approve", json={"job_id": JOB, "kind": "answer"}, headers=h)
    assert r.status_code == 403
    assert "only propose" in r.json()["error"]


def test_bad_token_rejected(sample):
    r = _client(sample).post(f"/api/{USER}/job_list", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 403


def test_validation_errors_are_explained(sample, owner):
    token = invoke(sample.app, owner, "token_issue", {"actor": "owner"})["token"]
    r = _client(sample).post(f"/api/{USER}/event_append", headers={"Authorization": f"Bearer {token}"},
                             json={"job_id": JOB, "event": {"date": "2026-11-05", "type": "stage_change", "stage": "accepted"}})
    assert r.status_code == 422 and "can't move" in r.json()["problems"][0]["message"]


def test_website_pages_render(sample, owner):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    c = _client(sample)
    for path in ["/", f"/u/{USER}/", f"/u/{USER}/applications", f"/u/{USER}/applications?stage=interested&sort=stage",
                 f"/u/{USER}/jobs/{JOB}", f"/u/{USER}/companies/northwind-labs", f"/u/{USER}/dashboard",
                 f"/u/{USER}/snapshots/{sid}.html"]:
        r = c.get(path)
        assert r.status_code == 200, (path, r.text[:300])
    assert "Platform Automation Engineer" in c.get(f"/u/{USER}/jobs/{JOB}").text
