"""End-to-end with the fictional user: jobs, honesty checks, snapshots, tracker (PRD 8.3–8.12)."""

from datetime import date

import pytest

from resume_builder.errors import Conflict, ValidationFailed
from resume_builder.operations import invoke
from resume_builder.slug import build_job_id
from resume_builder.services.render import export, load_snapshot, render_html

from .conftest import JOB, USER, data_file


def test_job_id_format():
    d = date(2026, 11, 13)
    assert build_job_id(d, "acme-corp", "platform-engineering", set()) == "2026-11-13-acme-corp-platform-engineering"
    assert build_job_id(d, "acme-corp", None, {"2026-11-13-acme-corp"}) == "2026-11-13-acme-corp-2"
    long_id = build_job_id(d, "a" * 40, "b" * 60, set())
    assert len(long_id) <= 80 and long_id.startswith("2026-11-13-" + "a" * 40)


def test_new_job_uses_registry_and_catches_near_duplicates(sample, owner, roots):
    out = invoke(sample.app, owner, "job_new", {"company": "Northwind Laboratories", "department": "Platform Engineering"})
    assert out["job_id"].endswith("-northwind-labs-platform-engineering")
    assert data_file(roots, out["folder"] + "application.yaml").exists()
    with pytest.raises(Conflict):
        invoke(sample.app, owner, "company_add", {"name": "Northwind Labs Inc."})


def test_preview_and_draft_rules(sample, owner):
    out = invoke(sample.app, owner, "render", {"job_key": "public", "export": "html"})
    assert out["snapshot_id"].startswith("sn-")
    # same input -> same snapshot id (stored once)
    again = invoke(sample.app, owner, "render", {"job_key": "public", "export": None})
    assert again["snapshot_id"] == out["snapshot_id"]


def test_answer_honesty_checks(sample, agent):
    bad = "I cut release time from 3 hours to 10 minutes using GitHub Actions [cite: fab-ci-pipeline]."
    with pytest.raises(ValidationFailed) as e:
        invoke(sample.app, agent, "document_propose", {"job_id": JOB, "kind": "answer", "text": bad})
    assert any("10" in p.message for p in e.value.problems)

    partial = "I cut release time to 5 minutes [cite: fab-ci-pipeline]."  # "5" is inside "25" in the bullet
    with pytest.raises(ValidationFailed):
        invoke(sample.app, agent, "document_propose", {"job_id": JOB, "kind": "answer", "text": partial})

    uncited = "I have used Kubernetes and Python for years."
    with pytest.raises(ValidationFailed) as e:
        invoke(sample.app, agent, "document_propose", {"job_id": JOB, "kind": "answer", "text": uncited})
    assert any("without a citation" in p.message for p in e.value.problems)

    not_on_resume = "I rebuilt the backups with weekly restore tests [cite: tail-backup]."
    with pytest.raises(ValidationFailed) as e:
        invoke(sample.app, agent, "document_propose", {"job_id": JOB, "kind": "answer", "text": not_on_resume})
    assert any("tailored resume" in p.message for p in e.value.problems)

    banned = "I am passionate about pipelines that cut release time from 3 hours to 25 minutes [cite: fab-ci-pipeline]."
    with pytest.raises(ValidationFailed):
        invoke(sample.app, agent, "document_propose", {"job_id": JOB, "kind": "answer", "text": banned})


def test_approve_send_and_protect(sample, owner):
    resume = invoke(sample.app, owner, "render", {"job_key": JOB, "export": "html"})["snapshot_id"]
    approved = invoke(sample.app, owner, "document_approve", {"job_id": JOB, "kind": "answer"})
    sid = approved["snapshot_id"]
    out = invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {
        "date": "2026-11-06", "type": "stage_change", "stage": "applied", "documents": [resume, sid]}})
    assert out["stage"] == "applied"
    app = sample.repo.get_application(JOB)
    assert app.submission.resume_snapshot == resume and sid in app.submission.answer_snapshots
    snaps = {s["id"]: s for s in invoke(sample.app, owner, "snapshot_list", {"job_key": JOB})}
    assert snaps[resume]["protected"] and snaps[resume]["reason"] == "sent"
    with pytest.raises(Conflict):
        invoke(sample.app, owner, "snapshot_delete", {"snapshot_id": resume})


def test_preview_cannot_be_sent(sample, owner):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "preview": True, "export": None})["snapshot_id"]
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {
            "date": "2026-11-06", "type": "stage_change", "stage": "applied", "documents": [sid]}})


def test_regeneration_is_exact_even_after_content_changes(sample, owner, roots):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    content, meta = load_snapshot(sample, sid)
    html1, _ = render_html(sample, content, meta)
    # Change the library after the fact.
    p = data_file(roots, "base.yaml")
    p.write_text(p.read_text().replace("Cut release time from 3 hours", "Reduced release time from 3 hours"))
    invoke(sample.app, owner, "sync", {})
    html2, _ = render_html(sample, *load_snapshot(sample, sid))
    assert html1 == html2 and "Cut release time" in html2
    new_sid = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    assert new_sid != sid
    diff = invoke(sample.app, owner, "snapshot_diff", {"a": sid, "b": new_sid})
    assert "-  - Cut release time" in diff and "+  - Reduced release time" in diff


def test_as_of_picks_latest_before(sample, owner):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    out = invoke(sample.app, owner, "regenerate", {"job_key": JOB, "doc_type": "resume", "as_of": "2999-01-01", "format": "html"})
    assert out["snapshot_id"] == sid
    with pytest.raises(Exception):
        invoke(sample.app, owner, "regenerate", {"job_key": JOB, "doc_type": "resume", "as_of": "2000-01-01", "format": "html"})


def test_snapshot_limits(sample, owner, roots):
    p = data_file(roots, "user.yaml")
    p.write_text(p.read_text().replace("max_per_job_and_type: 10", "max_per_job_and_type: 2"))
    invoke(sample.app, owner, "sync", {})
    base = data_file(roots, "base.yaml")
    ids = []
    for i in range(4):
        base.write_text(base.read_text().replace("Run a self-hosted", f"Run{i} a self-hosted") if i == 0 else
                        base.read_text().replace(f"Run{i - 1} a self-hosted", f"Run{i} a self-hosted"))
        invoke(sample.app, owner, "sync", {})
        ids.append(invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"])
    remaining = [s["id"] for s in invoke(sample.app, owner, "snapshot_list", {"job_key": JOB, "doc_type": "resume"})]
    assert remaining == ids[-2:]


def test_contact_details_never_in_snapshot(sample, owner):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    raw = sample.repo.get_snapshot(sid)["data"]
    assert "alex@example.com" not in raw
    html, _ = render_html(sample, *load_snapshot(sample, sid))
    assert "alex@example.com" in html


def test_public_site_has_no_contact_details(sample, owner):
    out = invoke(sample.app, owner, "publish_public", {})
    assert "alex@example.com" not in open(out["file"]).read()


def test_agent_brief_lists_only_confirmed_content(sample, agent):
    brief = invoke(sample.app, agent, "agent_brief", {"job_id": JOB})
    assert "fab-ci-pipeline" in brief and "Never invent" in brief and "Northwind" in brief


def test_dashboard_and_actions(sample, owner):
    resume = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {"date": "2026-11-06", "type": "stage_change", "stage": "applied", "documents": [resume]}})
    invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {"date": "2026-11-09", "type": "stage_change", "stage": "screening"}})
    invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {"date": "2026-11-12", "type": "stage_change", "stage": "interviewing"}})
    d = invoke(sample.app, owner, "dashboard", {})
    assert d["applied"] == 1 and d["response_rate"] == 1.0 and d["median_days_to_first_response"] == 3
    assert any(b == "fab-ci-pipeline" for b, _ in d["bullets_in_resumes_that_reached_interviews"])
    acts = invoke(sample.app, owner, "actions", {})
    assert acts["upcoming"][0]["job_id"] == JOB


def test_profile_change_flags_approved_answer(sample, owner):
    invoke(sample.app, owner, "document_approve", {"job_id": JOB, "kind": "answer"})
    profile = invoke(sample.app, owner, "profile_get", {"key": JOB})
    profile["roles"]["role-tailspin"] = ["tail-docker", "tail-backup"]
    invoke(sample.app, owner, "profile_write", {"key": JOB, "profile": profile})
    doc = invoke(sample.app, owner, "document_get", {"job_id": JOB, "kind": "answer"})
    assert doc["status"] == "draft" and "changed after this was approved" in doc["warnings"][0]["message"]


def test_audit_records_actor_and_channel(sample, owner, agent):
    invoke(sample.app, agent, "event_propose", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note"}})
    entries = invoke(sample.app, owner, "audit_list", {})
    assert any(e["actor"] == "agent" and e["operation"] == "event_propose" for e in entries)
    assert any(e["channel"] == "yaml-sync" for e in entries)
