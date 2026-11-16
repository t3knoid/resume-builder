"""Copy-ready rb commands: the Commands page, per-job next steps and `rb next` (issue #36).

Every command shown must parse with the real `rb` parser, so the page can't drift from the CLI.
Fictional data only.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from resume_builder.api import create_api
from resume_builder.cli import build_parser, main
from resume_builder.commands import Command, catalog, lines, quote
from resume_builder.operations import REGISTRY, invoke
from resume_builder.security import RequestContext

from .conftest import JOB, USER


def parses(c: dict | Command, user: str | None = None) -> None:
    c = c if isinstance(c, Command) else Command.from_dict(c)
    build_parser().parse_args((["--user", user] if user else []) + list(c.argv))


def all_commands(steps: list[dict]) -> list[dict]:
    return [c for s in steps for c in s["commands"]]


def steps(sample, owner, job=JOB) -> list[dict]:
    return invoke(sample.app, owner, "job_next_steps", {"job_id": job})


@pytest.fixture
def client(sample):
    return TestClient(create_api(sample.app), base_url="http://localhost")


# ---------------------------------------------------------------- the catalog

def test_every_catalog_command_parses_with_and_without_values():
    for job, snap in ((None, None), (JOB, "s-0001")):
        for group in catalog(job, snap):
            assert group["commands"], group["title"]
            for c in group["commands"]:
                parses(c)
                parses(c, user=USER)


def test_values_are_filled_in_and_placeholders_remain_only_where_unknown():
    text = " ".join(" ".join(c.argv) for g in catalog(JOB, "s-0001", "s-0002") for c in g["commands"])
    assert "<job-id>" not in text and "<snapshot-id>" not in text and JOB in text and "s-0001" in text
    assert "<link>" in text                                                # the app can't know it


def test_text_for_each_shell():
    assert lines(Command(("status",))) == [{"label": "", "text": "rb status"}]
    assert lines(Command(("status",)), "sam-lee")[0]["text"] == "rb --user sam-lee status"
    piped = lines(Command(("new-job", "--stdin", "<link>"), stdin_file="posting.txt"))
    assert [l["text"] for l in piped] == ["rb new-job --stdin <link> < posting.txt", "Get-Content posting.txt | rb new-job --stdin <link>"]
    clip = lines(Command(("propose", JOB, "profile"), clipboard=True))
    assert clip[1] == {"label": "Windows PowerShell", "text": f"Get-Clipboard | rb propose {JOB} profile"}


def test_quoting_works_in_both_shells():
    assert quote("2026-11-02-x") == "2026-11-02-x"
    assert quote("<what happened>") == '"<what happened>"'
    assert quote('say "hi" for $5') == '"say hi for 5"'                  # nothing either shell would expand


# ---------------------------------------------------------------- next steps

def test_the_operation_is_a_read_with_a_declared_permission(sample, owner, agent):
    op = REGISTRY.get("job_next_steps")
    assert op.permission == "application:read" and not op.writes
    reader = RequestContext(actor="viewer", roles=("viewer",), user_id=USER, channel="api")
    assert invoke(sample.app, reader, "job_next_steps", {"job_id": JOB}) == steps(sample, owner)
    assert isinstance(invoke(sample.app, agent, "job_next_steps", {"job_id": JOB}), list)


def test_sample_job_steps_parse(sample, owner):
    out = steps(sample, owner)
    assert out and all(s["text"] for s in out)
    for c in all_commands(out):
        parses(c)


def test_a_new_job_without_posting_or_notes(sample, owner):
    job = invoke(sample.app, owner, "job_new", {"company": "contoso-health", "title": "Site Reliability Engineer"})["job_id"]
    out = steps(sample, owner, job)
    keys = [s["key"] for s in out]
    assert keys[:3] == ["posting", "interest_notes", "tailor"]
    assert out[0]["file"] == f"jobs/{job}/posting.md" and out[1]["file"] == f"jobs/{job}/application.yaml"
    assert [c["argv"] for c in out[2]["commands"]][0] == ["brief", job]
    for c in all_commands(out):
        parses(c)


def test_draft_items_in_the_profile_are_listed_to_confirm(sample, owner, agent):
    profile = sample.repo.get_profile(JOB)
    role = next(iter(profile.roles))
    invoke(sample.app, agent, "bullet_propose", {"parent_id": role, "bullet": {
        "id": "draft-example", "text": "Cut deploy time from 40 to 12 minutes with cached Docker layers.", "status": "draft"}})
    profile.roles[role].append("draft-example")
    invoke(sample.app, owner, "profile_write", {"key": JOB, "profile": profile.model_dump(mode="json")})
    step = next(s for s in steps(sample, owner) if s["key"] == "drafts")
    argvs = [c["argv"] for c in step["commands"]]
    assert ["confirm", "draft-example"] in argvs and ["render", JOB, "--preview"] in argvs
    assert step["file"] == "base.yaml" and "render" not in [s["key"] for s in steps(sample, owner)]
    for c in step["commands"]:
        parses(c)


def test_after_rendering_the_applied_event_names_the_snapshot(sample, owner):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    step = next(s for s in steps(sample, owner) if s["key"] == "applied")
    argv = step["commands"][0]["argv"]
    assert argv[:5] == ["event", JOB, "--stage", "applied", "--documents"] and sid in argv
    parses(step["commands"][0])


def test_after_applying_only_tracking_steps(sample, owner):
    invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {"date": "2026-11-10", "type": "stage_change", "stage": "applied"}})
    out = steps(sample, owner)
    assert [s["key"] for s in out] in (["record"], ["stale"])
    for c in all_commands(out):
        parses(c)


def test_proposed_events_closed_and_archived_jobs(sample, owner, agent):
    invoke(sample.app, agent, "event_propose", {"job_id": JOB, "event": {"date": "2026-11-05", "type": "note", "notes": "x"}})
    first = steps(sample, owner)[0]
    assert first["key"] == "event_drafts" and first["commands"][0]["argv"][0] == "confirm-event"
    invoke(sample.app, owner, "event_append", {"job_id": JOB, "event": {"date": "2026-11-11", "type": "stage_change", "stage": "withdrawn"}})
    assert steps(sample, owner)[-1]["commands"][0]["argv"] == ["archive-job", JOB]
    invoke(sample.app, owner, "job_archive", {"job_id": JOB})
    assert steps(sample, owner)[-1]["commands"][0]["argv"] == ["archive-job", JOB, "--restore"]
    for c in all_commands(steps(sample, owner)):
        parses(c)


def test_unknown_job(sample, owner):
    from resume_builder.errors import NotFound
    with pytest.raises(NotFound):
        steps(sample, owner, "2026-01-01-nobody")


def test_rb_next_prints_the_steps(sample, capsys):
    assert main(["--user", USER, "next", JOB]) == 0
    out = capsys.readouterr().out
    assert out.startswith("1. ") and f"--user {USER}" in out


# ---------------------------------------------------------------- the website

def test_commands_page(client):
    r = client.get(f"/u/{USER}/commands")
    assert r.status_code == 200
    assert f"rb brief {JOB}" in r.text and 'class="copy"' in r.text and "Next for" in r.text
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert f'href="/u/{USER}/commands"' in client.get(f"/u/{USER}/").text          # header link
    assert client.get(f"/u/{USER}/commands?job=2026-01-01-nobody").status_code == 404


def test_job_page_shows_next_steps(client):
    page = client.get(f"/u/{USER}/jobs/{JOB}").text
    assert "Next steps" in page and f"/u/{USER}/commands?job={JOB}" in page


def test_no_user_flag_for_the_only_user(client):
    assert "--user" not in client.get(f"/u/{USER}/commands").text


def test_the_copy_script_is_served_and_loaded(client):
    js = client.get("/static/copy.js")
    assert js.status_code == 200 and "navigator.clipboard" in js.text
    assert '<script src="/static/copy.js" defer></script>' in client.get(f"/u/{USER}/commands").text


def test_catalog_exports_the_cover_letter():
    argvs = [list(c.argv) for g in catalog(JOB, "s-0001", "s-0002") for c in g["commands"]]
    assert ["render", JOB, "--type", "cover_letter"] in argvs
    assert ["regenerate", "s-0002"] in argvs and ["regenerate", "s-0001"] in argvs
    assert ["regenerate", "<snapshot-id>"] in [list(c.argv) for g in catalog(JOB, "s-0001") for c in g["commands"]]


def test_an_approved_document_gets_an_export_step(sample, owner):
    sid = invoke(sample.app, owner, "document_approve", {"job_id": JOB, "kind": "answer", "slug": "why-here"})["snapshot_id"]
    out = steps(sample, owner)
    step = next(s for s in out if s["key"] == "export:answer:why-here")
    assert step["commands"][0]["argv"] == ["regenerate", sid, "--format", "txt"]
    assert not any(s["key"].startswith("approve:answer") for s in out)
    resume = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None})["snapshot_id"]
    applied = next(s for s in steps(sample, owner) if s["key"] == "applied")["commands"][0]["argv"]
    assert resume in applied and sid in applied
    for c in all_commands(steps(sample, owner)):
        parses(c)
