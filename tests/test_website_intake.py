"""The website's view of job intake: saved postings and jobs proposed by the agent (PRD R13.8, R13.23).

The website is read-only (PRD 8.8): it shows proposals and the commands that confirm or discard them.
Fictional employers only; fetching uses a stub.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from resume_builder.api import create_api
from resume_builder.errors import PermissionDenied
from resume_builder.fetch import FetchedPage
from resume_builder.operations import invoke
from resume_builder.security import RequestContext

from .conftest import USER

FIXTURES = Path(__file__).parent / "fixtures" / "postings"
URL = "https://careers.example.com/jobs/platform-engineer"

HOSTILE = ("<b onmouseover=alert(1)>Platform Engineer</b>\n\n"
           "<script>alert('stolen')</script> <img src=x onerror=alert(2)> "
           + "We build internal developer tools and run the CI/CD platform for every team. " * 6)


class StubFetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url, limits):
        return FetchedPage(url=url, final_url=url, content_type="text/html", text=self.pages[url],
                           fetched_at="2026-11-15T10:00:00+00:00")


@pytest.fixture
def client(sample):
    sample.app.fetcher = StubFetcher({URL: (FIXTURES / "typical.html").read_text(encoding="utf-8")})
    return TestClient(create_api(sample.app), base_url="http://localhost")


def new_job_from(sample, owner, **source):
    p = invoke(sample.app, owner, "job_intake_preview", source)
    return invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "fields": p["fields"],
                                                    "company": "northwind-labs"})["job_id"]


def assert_no_live_markup(html: str):
    assert "<script>alert" not in html and "onerror=alert" not in html.replace("onerror=alert(2)&gt;", "")
    assert "<b onmouseover" not in html and "<img src=x" not in html


# ---------------------------------------------------------------- saved copies of the posting

def test_job_page_shows_saved_copies_as_text(sample, owner, client):
    job = new_job_from(sample, owner, url=URL)
    html = client.get(f"/u/{USER}/jobs/{job}").text
    assert "Saved copies of the posting" in html and "2026-11-15" in html
    assert "### What you will do" in html                    # the converted text...
    assert "onetrust-banner-sdk" not in html                 # ...never the fetched page itself
    assert f'<a href="{URL}" rel="noreferrer noopener">{URL}</a>' in html


def test_hostile_posting_text_is_escaped(sample, owner, client):
    job = new_job_from(sample, owner, text=HOSTILE, url="https://careers.example.com/x")
    html = client.get(f"/u/{USER}/jobs/{job}").text
    assert "&lt;script&gt;alert(&#39;stolen&#39;)&lt;/script&gt;" in html
    assert_no_live_markup(html)


def test_newest_copy_first(sample, owner, client):
    job = new_job_from(sample, owner, url=URL)
    p = invoke(sample.app, owner, "job_intake_preview", {"text": HOSTILE})
    invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "recapture": job, "replace_posting": True})
    html = client.get(f"/u/{USER}/jobs/{job}").text
    assert html.index("Pasted text") < html.index(URL + "</a>") and '<span class="pill">latest</span>' in html


def test_only_web_links_are_clickable(sample, owner, client):
    p = invoke(sample.app, owner, "job_intake_preview", {"url": URL})
    job = invoke(sample.app, owner, "job_intake", {"capture_id": p["capture_id"], "company": "northwind-labs",
                                                    "overrides": {"posting_url": "javascript:alert(document.cookie)"}})["job_id"]
    html = client.get(f"/u/{USER}/jobs/{job}").text
    assert 'href="javascript:' not in html


def test_captures_are_readable_without_a_token_but_raw_pages_are_not_offered(sample, owner, client):
    job = new_job_from(sample, owner, url=URL)
    rows = client.post(f"/api/{USER}/posting_captures", json={"job_id": job}).json()
    assert rows[0]["markdown"].startswith("## Platform Engineer") and "raw_content" not in rows[0]


# ---------------------------------------------------------------- proposed jobs

def test_proposals_appear_and_disappear(sample, owner, agent, client):
    a = invoke(sample.app, agent, "job_intake_propose", {"url": URL, "note": "Looks like a fit"})["proposal_id"]
    b = invoke(sample.app, agent, "job_intake_propose", {"text": HOSTILE})["proposal_id"]
    html = client.get(f"/u/{USER}/proposals").text
    assert f"#{a} · Platform Engineer" in html and "Looks like a fit" in html
    assert f"rb confirm-job {a}" in html and f"rb discard-job {b}" in html
    assert "Remote, United States" in html and "Location | Remote, United States" in html   # value and passage
    assert_no_live_markup(html)
    home = client.get(f"/u/{USER}/").text
    assert "2 job(s) proposed by your agent" in home and f'href="/u/{USER}/proposals"' in home

    invoke(sample.app, owner, "job_intake_confirm", {"proposal_id": a, "company": "northwind-labs"})
    invoke(sample.app, owner, "job_intake_discard", {"proposal_id": b})
    html = client.get(f"/u/{USER}/proposals").text
    assert "No jobs waiting" in html and f"#{a} · " not in html and f"rb confirm-job {a}" not in html
    assert "proposed by your agent" not in client.get(f"/u/{USER}/").text


def test_duplicate_proposal_shows_the_recapture_command(sample, agent, client):
    sample.app.fetcher.pages["https://example.com/jobs/1234"] = (FIXTURES / "heavy-nav.html").read_text(encoding="utf-8")
    pid = invoke(sample.app, agent, "job_intake_propose", {"url": "https://example.com/jobs/1234"})["proposal_id"]
    html = client.get(f"/u/{USER}/proposals").text
    assert f"rb confirm-job {pid} --recapture 2026-11-02-northwind-labs-platform-engineering" in html


def test_the_website_cannot_confirm_or_discard(sample, agent, client):
    pid = invoke(sample.app, agent, "job_intake_propose", {"url": URL})["proposal_id"]
    for op in ("job_intake_confirm", "job_intake_discard"):
        r = client.post(f"/api/{USER}/{op}", json={"proposal_id": pid, "company": "northwind-labs"})
        assert r.status_code == 403
    with pytest.raises(PermissionDenied):
        invoke(sample.app, RequestContext.anonymous(USER), "job_intake_confirm", {"proposal_id": pid})
    assert sample.repo.get_intake_proposal(pid)["status"] == "pending"
