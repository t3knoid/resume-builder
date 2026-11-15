"""Captured pages to posting.md without changing the wording (PRD R13.3, R13.11, R13.12).

Fixture pages in tests/fixtures/postings/ are fictional employers only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from resume_builder import postingtext
from resume_builder.errors import Conflict, ValidationFailed
from resume_builder.fetch import FetchedPage, FetchFailed
from resume_builder.operations import invoke
from resume_builder.postingtext import CaptureRefused, convert, html_to_markdown
from resume_builder.services.intake import attach_posting, capture_from_page, capture_from_text

from .conftest import JOB, data_file

FIXTURES = Path(__file__).parent / "fixtures" / "postings"
SEED_POSTING = Path(__file__).resolve().parent.parent / "src" / "resume_builder" / "templates" / "seed" / "posting.md"


def page(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.lower())


def is_subsequence(small: list[str], big: list[str]) -> bool:
    it = iter(big)
    return all(w in it for w in small)


# ---------------------------------------------------------------- conversion

def test_typical_page_keeps_the_posting_and_drops_the_chrome():
    md = convert(page("typical.html"), "text/html")
    assert "## Platform Engineer" in md                      # headings move down one level under "# Job posting"
    assert "### What you will do" in md
    assert ("The Platform team at Northwind Labs builds the tools our engineers use to ship software safely. "
            "You will work on CI/CD pipelines, observability and developer self-service.") in md
    assert "- Design and maintain **GitHub Actions** workflows for 40+ services." in md
    assert "  - Own alert routing and on-call dashboards." in md
    assert "- Automate server builds with *Ansible* and Python." in md
    assert "1. 4+ years in infrastructure automation or platform engineering." in md
    assert "Posted on 2026-11-02\\\nClosing date: 2026-12-15" in md   # <br> becomes a hard line break
    assert "Salary range | $140,000 – $165,000" in md
    assert "\\# of on-call weeks per quarter: two." in md   # plain text is never turned into a heading
    for chrome in ("cookies", "All jobs", "Benefits", "Sign in", "All rights reserved", "Privacy policy",
                   "Apply now", "analytics", "font-family"):
        assert chrome not in md, chrome


def test_wording_is_never_changed():
    """Every word of the Markdown appears in the page's text, in the same order: nothing is added,
    reworded or reordered."""
    for name in ("typical.html", "heavy-nav.html"):
        html = page(name)
        visible = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        visible = re.sub(r"<[^>]+>", " ", visible).replace("&middot;", "·").replace("&ndash;", "–").replace("&copy;", "©")
        md = re.sub(r"^\s*\d+\. ", "", convert(html, "text/html"), flags=re.M)  # list numbers are formatting
        assert is_subsequence(words(md), words(visible)), name


def test_page_without_main_uses_the_article_and_skips_menus():
    md = convert(page("heavy-nav.html"), "text/html")
    assert md.startswith("## Data Analyst\n")
    assert "- Build weekly reports on patient wait times using SQL and Power BI." in md
    assert "This is an onsite role. Salary: $70,000 - $82,000 per year." in md
    for chrome in ("Veterans", "Life at Contoso", "Careers / Analytics", "Similar jobs", "Share this job"):
        assert chrome not in md, chrome


@pytest.mark.parametrize("name,reason", [("js-shell.html", "needs-javascript"), ("login.html", "login-page")])
def test_unusable_pages_are_refused(name, reason):
    with pytest.raises(CaptureRefused) as e:
        convert(page(name), "text/html")
    assert e.value.reason == reason


def test_banner_class_inside_the_main_content_is_kept():
    html = page("typical.html").replace("<header><h1>Platform Engineer</h1>", '<header class="job-banner"><h1>Platform Engineer</h1>')
    html = html.replace("<main>", '<main><p class="visually-hidden">Skip to content</p>')
    md = convert(html, "text/html")
    assert md.startswith("## Platform Engineer\n") and "Skip to content" not in md


def test_tiny_page_is_refused():
    with pytest.raises(CaptureRefused) as e:
        convert("<html><body><h1>Oops</h1><p>Something went wrong.</p></body></html>", "text/html")
    assert e.value.reason == "too-little-text"


def test_javascript_page_with_jobposting_data_uses_its_description():
    c = html_to_markdown(page("js-shell-jsonld.html"))
    assert c.json_ld_description and not c.needs_javascript
    assert c.markdown.startswith("## Site Reliability Engineer\n")
    assert "- Run incident response and write blameless post-incident reviews." in c.markdown
    assert "**You will:**" in c.markdown
    assert convert(page("js-shell-jsonld.html"), "text/html") == c.markdown


def test_plain_text_is_kept_as_is():
    text = "Platform Engineer\r\n\r\n  Indented line stays indented.   \r\n- a list\r\n\r\n\r\n\r\nEnd"
    assert convert(text, "text/plain") == "Platform Engineer\n\n  Indented line stays indented.\n- a list\n\nEnd\n"
    with pytest.raises(CaptureRefused):
        convert("  \n\n ", "text/plain")


# ---------------------------------------------------------------- posting.md

def test_posting_document_layout_and_notes():
    doc = postingtext.posting_document("## Title\n\nBody.\n", "https://jobs.example.com/1", "2026-11-15", notes="Ask about on-call.")
    assert doc == ("# Job posting\n\n## Title\n\nBody.\n\n## Source\n\nhttps://jobs.example.com/1, captured 2026-11-15.\n\n"
                   "## My notes\n\nAsk about on-call.\n")
    assert postingtext.notes_of(doc) == "Ask about on-call."
    assert postingtext.body_of(doc) == "## Title\n\nBody."
    hinted = postingtext.posting_document("x", "y", "2026-11-15")
    assert postingtext.notes_of(hinted) is None


def test_seed_hints_match_the_seeded_posting():
    seed = SEED_POSTING.read_text(encoding="utf-8")
    assert postingtext.NOTES_HINT in seed
    assert postingtext.SEED_BODY_HINT in seed
    body_only = seed.split("-->", 1)[1]
    assert postingtext.body_of(body_only) == ""


# ---------------------------------------------------------------- captures and the job's posting.md

def fetched(name: str, url: str = "https://jobs.example.com/platform-engineer") -> FetchedPage:
    return FetchedPage(url=url, final_url=url, content_type="text/html", text=page(name), fetched_at="2026-11-15T10:00:00+00:00")


def new_job(app, owner) -> str:
    return invoke(app, owner, "job_new", {"company": "northwind-labs", "title": "Platform Engineer"})["job_id"]


def test_capture_of_an_unusable_page_fails_with_the_paste_fallback():
    with pytest.raises(FetchFailed) as e:
        capture_from_page(fetched("login.html"))
    assert e.value.reason == "login-page" and "rb new-job --stdin" in str(e.value)


def test_capture_on_a_new_job_writes_posting_md(sample, owner, roots):
    job = new_job(sample.app, owner)
    out = attach_posting(sample, owner, job, capture_from_page(fetched("typical.html")))
    assert out["file_updated"] and out["capture_id"]
    text = data_file(roots, f"jobs/{job}/posting.md").read_text(encoding="utf-8")
    assert text.startswith("# Job posting\n\n## Platform Engineer\n")
    assert "## Source\n\nhttps://jobs.example.com/platform-engineer, captured 2026-11-15." in text
    assert "## My notes\n\n" + postingtext.NOTES_HINT in text
    assert sample.repo.latest_posting_capture(job).id == out["capture_id"]
    invoke(sample.app, owner, "sync", {})  # the written file is already in step with the database
    assert sample.repo.sync_file(f"jobs/{job}/posting.md")["result"] == "applied"
    assert sample.repo.application_row(job)["posting_text"] == text


def test_recapture_replaces_the_posting_and_keeps_my_notes(sample, owner, roots):
    job = new_job(sample.app, owner)
    attach_posting(sample, owner, job, capture_from_page(fetched("typical.html")))
    path = data_file(roots, f"jobs/{job}/posting.md")
    path.write_text(path.read_text(encoding="utf-8").replace(postingtext.NOTES_HINT, "Recruiter said the team is six people."), encoding="utf-8")
    invoke(sample.app, owner, "sync", {})
    attach_posting(sample, owner, job, capture_from_page(fetched("heavy-nav.html")))
    text = path.read_text(encoding="utf-8")
    assert "## Data Analyst" in text and "Platform Engineer" not in text
    assert text.endswith("## My notes\n\nRecruiter said the team is six people.\n")
    assert len(sample.repo.list_posting_captures(job)) == 2


def test_posting_text_the_user_wrote_is_not_replaced(sample, owner, roots):
    # The example job's posting.md holds text the user pasted by hand, and no capture.
    before = sample.repo.application_row(JOB)["posting_text"]
    with pytest.raises(Conflict):
        attach_posting(sample, owner, JOB, capture_from_page(fetched("typical.html")))
    assert sample.repo.application_row(JOB)["posting_text"] == before
    assert sample.repo.list_posting_captures(JOB) == []
    out = attach_posting(sample, owner, JOB, capture_from_page(fetched("typical.html")), replace=True)
    assert out["file_updated"]
    assert "## Platform Engineer" in data_file(roots, f"jobs/{JOB}/posting.md").read_text(encoding="utf-8")


def test_posting_edited_after_capture_is_not_replaced(sample, owner, roots):
    job = new_job(sample.app, owner)
    attach_posting(sample, owner, job, capture_from_page(fetched("typical.html")))
    path = data_file(roots, f"jobs/{job}/posting.md")
    path.write_text(path.read_text(encoding="utf-8").replace("Platform Engineer\n", "Platform Engineer (II)\n", 1), encoding="utf-8")
    invoke(sample.app, owner, "sync", {})
    with pytest.raises(Conflict):
        attach_posting(sample, owner, job, capture_from_page(fetched("heavy-nav.html")))


def test_unsynced_edits_are_left_alone_and_become_a_conflict(sample, owner, roots):
    job = new_job(sample.app, owner)
    attach_posting(sample, owner, job, capture_from_page(fetched("typical.html")))
    path = data_file(roots, f"jobs/{job}/posting.md")
    edited = path.read_text(encoding="utf-8") + "\nA line I added and haven't synced.\n"
    path.write_text(edited, encoding="utf-8")
    out = attach_posting(sample, owner, job, capture_from_page(fetched("heavy-nav.html")), replace=True)
    assert out["file_updated"] is False and "rb resolve" in out["note"]
    assert path.read_text(encoding="utf-8") == edited
    invoke(sample.app, owner, "sync", {})
    assert sample.repo.sync_file(f"jobs/{job}/posting.md")["result"] == "conflict"


def test_pasted_text_capture(sample, owner, roots):
    job = new_job(sample.app, owner)
    cap = capture_from_text("Platform Engineer\n\nWe automate everything.\n", source_kind="file", source_path="intake/job.txt")
    attach_posting(sample, owner, job, cap)
    text = data_file(roots, f"jobs/{job}/posting.md").read_text(encoding="utf-8")
    assert "# Job posting\n\nPlatform Engineer\n\nWe automate everything.\n\n## Source\n\nPasted text (intake/job.txt), captured " in text
    with pytest.raises(ValidationFailed):
        capture_from_text("   ")


def test_capture_is_audited(sample, owner):
    job = new_job(sample.app, owner)
    out = attach_posting(sample, owner, job, capture_from_page(fetched("typical.html")))
    entry = next(e for e in sample.repo.audit_entries() if e["operation"] == "posting:capture")
    assert entry["target"] == job and entry["result"] == f"capture {out['capture_id']}"
