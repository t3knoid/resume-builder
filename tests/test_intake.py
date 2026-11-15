"""Job intake end to end: preview, review, create (PRD R13.1, R13.19–R13.21, R13.25, R13.26).

On the command line, intake is `rb new-job` given the posting's link or `--stdin`.

Fictional employers only; fetching uses a stub, never the network.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from resume_builder import postingtext
from resume_builder.cli import main
from resume_builder.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from resume_builder.fetch import FetchedPage, FetchFailed
from resume_builder.operations import invoke

from .conftest import JOB, USER, data_file

FIXTURES = Path(__file__).parent / "fixtures" / "postings"
URL = "https://careers.example.com/jobs/platform-engineer"


class StubFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def fetch(self, url, limits):
        self.calls.append(url)
        if url not in self.pages:
            raise FetchFailed("not-found", "No such page.")
        return FetchedPage(url=url, final_url=url, content_type="text/html", text=self.pages[url],
                           fetched_at="2026-11-15T10:00:00+00:00")


def page(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def site(sample):
    stub = StubFetcher({URL: page("typical.html"),
                        "https://jobs.fabrikam.example.com/sre": page("structured.html"),
                        "https://example.com/jobs/1234?utm_source=feed": page("heavy-nav.html"),
                        "https://careers.example.com/login": page("login.html")})
    sample.app.fetcher = stub
    return stub


def preview(app, ctx, **kw):
    return invoke(app, ctx, "job_intake_preview", kw)


def fields_of(p):
    return {f["name"]: f["value"] for f in p["fields"]}


# ---------------------------------------------------------------- preview

def test_preview_captures_once_and_creates_nothing_else(sample, owner, site):
    jobs, reg = sample.repo.job_ids(), sample.repo.load_registry()
    p = preview(sample.app, owner, url=URL)
    assert site.calls == [URL]
    assert fields_of(p)["title"] == "Platform Engineer" and fields_of(p)["location"] == "Remote, United States"
    assert p["company"] == {"name": None, "candidates": [], "suggested": None, "new_slug": None}
    assert p["job_id"] is None and p["duplicates"] == []
    cap = sample.repo.get_posting_capture(p["capture_id"])
    assert cap.job_id is None and cap.source_url == URL
    assert sample.repo.job_ids() == jobs and sample.repo.load_registry() == reg


def test_repeating_a_preview_reuses_the_capture(sample, owner, site):
    assert preview(sample.app, owner, url=URL)["capture_id"] == preview(sample.app, owner, url=URL)["capture_id"]


def test_preview_suggests_a_new_company_and_the_job_id(sample, owner, site):
    p = preview(sample.app, owner, url="https://jobs.fabrikam.example.com/sre")
    assert p["company"]["name"] == "Fabrikam Inc." and p["company"]["suggested"] is None
    assert p["company"]["new_slug"] == "fabrikam-inc"
    assert p["job_id"].endswith("-fabrikam-inc")
    assert any("date_posted" in d for d in p["disagreements"])


def test_unusable_page_stores_nothing(sample, owner, site):
    with pytest.raises(FetchFailed) as e:
        preview(sample.app, owner, url="https://careers.example.com/login")
    assert e.value.reason == "login-page"
    assert sample.repo.conn.execute("SELECT COUNT(*) FROM posting_captures").fetchone()[0] == 0


def test_preview_needs_a_link_or_text(sample, owner):
    with pytest.raises(Exception):
        preview(sample.app, owner)


# ---------------------------------------------------------------- create

def test_intake_creates_the_job_with_fields_and_posting(sample, owner, site, roots):
    p = preview(sample.app, owner, url=URL)
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"],
                                                    "company": "northwind-labs"})
    job = out["job_id"]
    app = sample.repo.get_application(job)
    assert app.company == "northwind-labs" and app.title == "Platform Engineer"
    assert app.location == "Remote, United States" and app.work_arrangement == "remote"
    assert app.salary_range == "$140,000 – $165,000" and str(app.date_posted) == "2026-11-02"
    assert app.external_id == "NW-4821" and app.posting_url == URL and app.source == "company_site"
    assert sample.repo.application_row(job)["stage"] == "interested"
    assert sample.repo.latest_posting_capture(job).id == p["capture_id"]
    yaml_text = data_file(roots, f"jobs/{job}/application.yaml").read_text(encoding="utf-8")
    assert "title: Platform Engineer" in yaml_text and "work_arrangement: remote" in yaml_text
    posting = data_file(roots, f"jobs/{job}/posting.md").read_text(encoding="utf-8")
    assert posting.startswith("# Job posting\n\n## Platform Engineer") and f"## Source\n\n{URL}, captured" in posting
    assert any("interest_notes" in s for s in out["next"]) and any(f"rb brief {job}" in s for s in out["next"])
    assert out["created"] == {}
    # The written files are already in step with the database.
    invoke(sample.app, owner, "sync", {})
    assert sample.repo.get_application(job).title == "Platform Engineer"


def test_new_company_only_when_asked(sample, owner, site, roots):
    p = preview(sample.app, owner, url="https://jobs.fabrikam.example.com/sre")
    with pytest.raises(NotFound):
        invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"]})
    assert "fabrikam-inc" not in {c.slug for c in sample.repo.load_registry().companies}
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"], "create_missing": True})
    assert out["created"] == {"company": "fabrikam-inc"}
    assert sample.repo.get_application(out["job_id"]).title == "Senior Site Reliability Engineer"
    assert "slug: fabrikam-inc" in data_file(roots, "organizations.yaml").read_text(encoding="utf-8")


def test_company_is_required(sample, owner, site):
    p = preview(sample.app, owner, url=URL)
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"]})


def test_close_company_name_resolves_to_the_registry_entry(sample, owner, site):
    p = preview(sample.app, owner, url=URL)
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "company": "Northwind Labs, Inc."})
    assert out["job_id"].endswith("-northwind-labs") and out["created"] == {}


def test_owner_overrides(sample, owner, site):
    p = preview(sample.app, owner, url=URL)
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"], "company": "northwind-labs",
                                                    "overrides": {"work_arrangement": "hybrid", "salary_range": None}})
    app = sample.repo.get_application(out["job_id"])
    assert app.work_arrangement == "hybrid" and app.salary_range is None


def test_invalid_override_is_refused(sample, owner, site):
    p = preview(sample.app, owner, url=URL)
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "company": "northwind-labs",
                                                 "overrides": {"work_arrangement": "sometimes"}})


def test_fields_are_checked_again(sample, owner, site):
    p = preview(sample.app, owner, url=URL)
    tampered = [dict(f, value="$400,000 – $500,000") if f["name"] == "salary_range" else f for f in p["fields"]]
    jobs = sample.repo.job_ids()
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": tampered, "company": "northwind-labs"})
    assert sample.repo.job_ids() == jobs


def test_a_capture_creates_one_job_only(sample, owner, site):
    p = preview(sample.app, owner, url=URL)
    invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "company": "northwind-labs"})
    with pytest.raises(Conflict):
        invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "company": "northwind-labs"})


def test_department_named_in_the_posting(sample, owner):
    text = (FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8")
    p = preview(sample.app, owner, text=text)
    assert p["department"]["name"] == "INFORMATION TECHNOLOGY DIVISION"
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"],
                                                    "company": "City of Springfield", "create_missing": True})
    assert out["created"] == {"company": "city-of-springfield", "department": "information-technology-division"}
    assert sample.repo.get_application(out["job_id"]).department == "information-technology-division"


def test_department_not_in_registry_is_left_out_unless_asked(sample, owner):
    text = (FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8")
    p = preview(sample.app, owner, text=text)
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"], "company": "contoso-health"})
    assert sample.repo.get_application(out["job_id"]).department is None
    assert "INFORMATION TECHNOLOGY DIVISION" in out["note"]


def test_all_or_nothing(sample, owner, site, monkeypatch):
    p = preview(sample.app, owner, url="https://jobs.fabrikam.example.com/sre")
    jobs = sample.repo.job_ids()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(sample.repo, "set_posting", boom)
    with pytest.raises(RuntimeError):
        invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"], "create_missing": True})
    monkeypatch.undo()
    assert sample.repo.job_ids() == jobs
    assert "fabrikam-inc" not in {c.slug for c in sample.repo.load_registry().companies}
    assert sample.repo.get_posting_capture(p["capture_id"]).job_id is None


# ---------------------------------------------------------------- duplicates and re-capture

def test_duplicate_is_flagged_and_refused(sample, owner, site):
    p = preview(sample.app, owner, url="https://example.com/jobs/1234?utm_source=feed")
    assert [d["job_id"] for d in p["duplicates"]] == [JOB]
    with pytest.raises(Conflict) as e:
        invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "company": "northwind-labs"})
    assert f"recapture `{JOB}`" in e.value.message
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "company": "northwind-labs", "allow_duplicate": True})
    assert out["job_id"] != JOB


def test_recapture_saves_the_posting_for_the_existing_job(sample, owner, site, roots):
    p = preview(sample.app, owner, url="https://example.com/jobs/1234?utm_source=feed")
    before = sample.repo.get_application(JOB)
    with pytest.raises(Conflict):  # the example job's posting.md holds text the user pasted
        invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "recapture": JOB})
    out = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "recapture": JOB, "replace_posting": True})
    assert out["recaptured"] and out["job_id"] == JOB
    assert "## Data Analyst" in data_file(roots, f"jobs/{JOB}/posting.md").read_text(encoding="utf-8")
    assert sample.repo.get_application(JOB) == before  # application fields untouched


# ---------------------------------------------------------------- permissions

def test_only_the_owner_runs_intake(sample, agent, site):
    with pytest.raises(PermissionDenied):
        invoke(sample.app, agent, "job_intake_preview", {"url": URL})
    with pytest.raises(PermissionDenied):
        invoke(sample.app, agent, "job_intake", {"capture_id": 1})
    assert site.calls == []


def test_intake_operations_declare_application_write(sample):
    for name in ("job_intake_preview", "job_intake"):
        op = sample.app.registry.get(name)
        assert op.permission == "application:write" and op.writes


def test_new_job_still_works(sample, owner):
    out = invoke(sample.app, owner, "job_new", {"company": "contoso-health", "title": "Analyst"})
    assert sample.repo.get_application(out["job_id"]).title == "Analyst"


# ---------------------------------------------------------------- rb new-job with a posting

PASTED = (FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8")


def run(monkeypatch, capsys, argv, stdin=""):
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = main(["--user", USER, *argv])
    return code, capsys.readouterr()


def test_cli_without_yes_only_previews(sample, monkeypatch, capsys):
    jobs = sample.repo.job_ids()
    code, out = run(monkeypatch, capsys, ["new-job", "--stdin", "--company", "contoso-health"], "﻿" + PASTED)
    assert code == 0
    assert "title             SYSTEMS ADMINISTRATOR III  [posting text]" in out.out
    assert "--yes" in out.out
    assert sample.repo.job_ids() == jobs


def test_cli_creates_the_job_with_yes(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["new-job", "--stdin", "--company", "City of Springfield", "--create",
                                          "--set", "work_arrangement=onsite", "--yes"], PASTED)
    assert code == 0, out.err
    assert "Created " in out.out and "Added company `city-of-springfield`" in out.out and "interest_notes" in out.out
    job = next(j for j in sample.repo.job_ids() if "city-of-springfield" in j)
    app = sample.repo.get_application(job)
    assert app.work_arrangement == "onsite" and app.external_id == "714052"


def test_cli_asks_for_a_company_when_none_matches(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["new-job", "--stdin", "--yes"], PASTED)
    assert code == 1 and "--company" in out.out


def test_cli_reports_a_duplicate(sample, monkeypatch, capsys):
    text = "https://example.com/jobs/1234\nPlatform Automation Engineer\n\n" + "We automate the platform. " * 20
    code, out = run(monkeypatch, capsys, ["new-job", "--stdin", "--company", "northwind-labs", "--yes"], text)
    assert code == 1 and f"--recapture {JOB}" in out.out


class Tty(io.StringIO):
    def isatty(self):
        return True


def test_cli_interactive_review(sample, monkeypatch, capsys):
    """A terminal session: fetch the link, add a new company, change one field, create."""
    from resume_builder.fetch import HttpPostingFetcher

    monkeypatch.setattr(HttpPostingFetcher, "fetch", lambda self, url, limits: StubFetcher({URL: page("typical.html")}).fetch(url, limits))
    answers = iter(["Tailspin Toys", "y", "location", "Denver, CO", "bogus", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("sys.stdin", Tty(""))
    code = main(["--user", USER, "new-job", URL])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "location          Denver, CO  [you]" in out and "Type one of:" in out
    job = next(j for j in sample.repo.job_ids() if "tailspin-toys" in j)
    app = sample.repo.get_application(job)
    assert app.location == "Denver, CO" and app.title == "Platform Engineer"


def test_cli_interactive_cancel_creates_nothing(sample, monkeypatch, capsys):
    from resume_builder.fetch import HttpPostingFetcher

    monkeypatch.setattr(HttpPostingFetcher, "fetch", lambda self, url, limits: StubFetcher({URL: page("typical.html")}).fetch(url, limits))
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("sys.stdin", Tty(""))
    jobs = sample.repo.job_ids()
    assert main(["--user", USER, "new-job", URL, "--company", "northwind-labs"]) == 1
    assert "Nothing was created" in capsys.readouterr().out
    assert sample.repo.job_ids() == jobs


def test_cli_rejects_a_bad_set(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["new-job", "--stdin", "--set", "interest_notes=love it", "--yes"], PASTED)
    assert code == 1 and "--set needs FIELD=VALUE" in out.err


def stub_fetch(monkeypatch):
    from resume_builder.fetch import HttpPostingFetcher

    stub = StubFetcher({URL: page("typical.html")})
    monkeypatch.setattr(HttpPostingFetcher, "fetch", lambda self, url, limits: stub.fetch(url, limits))
    return stub


def test_cli_url_option_is_the_same_as_the_positional_link(sample, monkeypatch, capsys):
    stub = stub_fetch(monkeypatch)
    code, out = run(monkeypatch, capsys, ["new-job", "--url", URL, "--company", "northwind-labs",
                                          "--title", "Platform Engineer II", "--source", "referral", "--yes"])
    assert code == 0, out.err
    assert stub.calls == [URL]
    job = next(j for j in sample.repo.job_ids() if j not in (JOB,) and "northwind-labs" in j)
    app = sample.repo.get_application(job)
    assert app.title == "Platform Engineer II" and app.source == "referral"   # new-job's options are your values
    assert app.location == "Remote, United States"                          # the rest is read from the posting


def test_cli_link_given_twice_differently_is_refused(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["new-job", URL, "--url", "https://careers.example.com/other", "--yes"])
    assert code == 1 and "once" in out.err


def test_cli_empty_stdin_is_refused(sample, monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, ["new-job", "--stdin", "--company", "northwind-labs", "--yes"], "   ")
    assert code == 1 and "Nothing came in on stdin" in out.err


def test_cli_without_a_posting_works_as_before(sample, monkeypatch, capsys):
    """No link and no --stdin: new-job only creates the job and seeds the folder; nothing is fetched."""
    stub = stub_fetch(monkeypatch)
    code, out = run(monkeypatch, capsys, ["new-job", "--company", "contoso-health", "--title", "Data Analyst"])
    assert code == 0, out.err
    assert stub.calls == []
    assert "Paste the job description into" in out.out
    job = next(j for j in sample.repo.job_ids() if "contoso-health" in j)
    assert sample.repo.get_application(job).title == "Data Analyst"
    assert sample.repo.list_posting_captures(job) == []
