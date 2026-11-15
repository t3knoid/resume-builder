"""Site adapters for job intake (PRD R13.16): selected by domain, and held to the same checks.

The adapters and sites here are fictional. No test touches the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resume_builder.adapters import AdapterCapture, adapter_for
from resume_builder.config import load_settings
from resume_builder.fetch import FetchedPage, FetchFailed, HttpPostingFetcher
from resume_builder.models import ExtractedField
from resume_builder.services.intake import capture_url
from resume_builder.space import App

FIXTURES = Path(__file__).parent / "fixtures" / "postings"
ATS_URL = "https://acme.jobs.example-ats.com/postings/42"


class StubFetcher:
    """Serves fixture pages by URL; records every request."""

    def __init__(self, pages: dict[str, str]):
        self.pages, self.calls = pages, []

    def fetch(self, url, limits):
        self.calls.append(url)
        if url not in self.pages:
            raise FetchFailed("not-found", "No such page.")
        return FetchedPage(url=url, final_url=url, content_type="text/html", text=self.pages[url],
                           fetched_at="2026-11-15T10:00:00+00:00")


def field(name, value, passage):
    return ExtractedField(name=name, value=value, passage=passage, origin="rule")


class ExampleAts:
    """A well-behaved adapter: fetches the posting and names the employer from the page."""

    name = "example-ats"
    domains = ("jobs.example-ats.com",)

    def __init__(self, fields=None, markdown=None, fetch_urls=None, return_page=None, explode=False):
        self.fields, self.markdown, self.fetch_urls = fields, markdown, fetch_urls
        self.return_page, self.explode = return_page, explode

    def capture(self, url, fetch):
        if self.explode:
            raise RuntimeError("selector changed")
        page = None
        for u in self.fetch_urls or [url]:
            page = fetch(u)
        if self.return_page is not None:
            page = self.return_page
        fields = self.fields if self.fields is not None else [
            field("company", "Northwind Labs", "The Platform team at Northwind Labs builds the tools")]
        return AdapterCapture(page=page, markdown=self.markdown, fields=fields)


def app_with(adapter, pages=None, fetcher=None, roots=None):
    app = App(load_settings(), plugins=[lambda ext: ext.add_site_adapter(adapter)],
              fetcher=fetcher or StubFetcher(pages or {ATS_URL: (FIXTURES / "typical.html").read_text(encoding="utf-8")}))
    return app


@pytest.fixture
def space_for(sample):
    """Builds an App with one adapter, on the synced example user's data."""
    apps = []

    def make(adapter, **kw):
        app = app_with(adapter, **kw)
        apps.append(app)
        return app.space(sample.user)

    yield make
    for a in apps:
        a.close()


# ---------------------------------------------------------------- registration and selection

def test_adapter_is_chosen_by_domain():
    a, b = ExampleAts(), type("Narrow", (ExampleAts,), {"name": "narrow", "domains": ("acme.jobs.example-ats.com",)})()
    assert adapter_for([a], ATS_URL) is a
    assert adapter_for([a], "https://jobs.example-ats.com/x") is a
    assert adapter_for([a], "https://example-ats.com/x") is None
    assert adapter_for([a], "https://notjobs.example-ats.com.evil.example/x") is None
    assert adapter_for([a, b], ATS_URL) is b  # the most specific domain wins


@pytest.mark.parametrize("bad", [
    object(),
    type("NoDomains", (ExampleAts,), {"domains": ()})(),
    type("Url", (ExampleAts,), {"domains": ("https://jobs.example-ats.com",)})(),
])
def test_invalid_adapters_are_refused_at_registration(roots, bad):
    with pytest.raises(TypeError):
        App(load_settings(), plugins=[lambda ext: ext.add_site_adapter(bad)])


def test_urls_without_an_adapter_use_the_generic_path(space_for, owner):
    stub = StubFetcher({"https://careers.example.com/1": (FIXTURES / "typical.html").read_text(encoding="utf-8")})
    space = space_for(ExampleAts(), fetcher=stub)
    out = capture_url(space, owner, "https://careers.example.com/1")
    assert out.adapter is None and out.extraction.fields["title"].value == "Platform Engineer"


# ---------------------------------------------------------------- an adapter's output is checked

def test_adapter_fields_come_first_and_are_marked(space_for, owner):
    space = space_for(ExampleAts())
    out = capture_url(space, owner, ATS_URL)
    assert out.adapter == "example-ats" and out.notes == []
    company = out.extraction.fields["company"]
    assert company.value == "Northwind Labs" and company.origin == "adapter"
    assert out.extraction.fields["title"].value == "Platform Engineer"   # core still fills the rest
    assert out.capture.source_url == ATS_URL
    audit = [e for e in space.repo.audit_entries() if e["operation"] == "posting:adapter"]
    assert audit[0]["target"] == "example-ats" and audit[0]["result"] == "ok"


def test_adapter_values_not_in_the_posting_are_dropped(space_for, owner):
    space = space_for(ExampleAts(fields=[field("salary_range", "$300,000 – $400,000", "Pay: $300,000 – $400,000"),
                                         field("title", "Staff Platform Engineer", "Platform Engineer")]))
    out = capture_url(space, owner, ATS_URL)
    assert out.extraction.fields["salary_range"].value == "$140,000 – $165,000"   # the posting's own, from core rules
    assert out.extraction.fields["title"].value == "Platform Engineer"
    assert len(out.notes) == 2 and all("doesn't state" in n for n in out.notes)


def test_adapter_markdown_must_keep_the_wording(space_for, owner):
    kept = "## Platform Engineer\n\nThe Platform team at Northwind Labs builds the tools our engineers use to ship software safely.\n"
    out = capture_url(space_for(ExampleAts(markdown=kept)), owner, ATS_URL)
    assert out.capture.markdown == kept and out.notes == []

    reworded = "## Platform Engineer\n\nNorthwind Labs is a fast-growing leader in developer tooling.\n"
    out = capture_url(space_for(ExampleAts(markdown=reworded)), owner, ATS_URL)
    assert "fast-growing" not in out.capture.markdown and "### What you will do" in out.capture.markdown
    assert any("changed the posting's wording" in n for n in out.notes)


# ---------------------------------------------------------------- an adapter fetches only through intake

def test_adapter_may_fetch_only_its_own_domains(space_for, owner):
    space = space_for(ExampleAts(fetch_urls=["https://tracker.example.net/pixel"]))
    with pytest.raises(FetchFailed) as e:
        capture_url(space, owner, ATS_URL)
    assert e.value.reason == "adapter-domain"


def test_adapter_request_limit(space_for, owner):
    space = space_for(ExampleAts(fetch_urls=[ATS_URL] * 3))
    with pytest.raises(FetchFailed) as e:
        capture_url(space, owner, ATS_URL)
    assert e.value.reason == "adapter-limit"
    assert len(space.app.fetcher.calls) == 2


def test_adapter_cannot_reach_an_internal_address(space_for, owner):
    """The adapter's fetch is the real safe fetcher: a name resolving to a private address is refused."""
    real = HttpPostingFetcher(resolve=lambda host, port: ["10.0.0.5"])
    space = space_for(ExampleAts(), fetcher=real)
    with pytest.raises(FetchFailed) as e:
        capture_url(space, owner, ATS_URL)
    assert e.value.reason == "address-refused"
    fetches = [x for x in space.repo.audit_entries() if x["operation"] == "posting:fetch"]
    assert fetches[0]["target"] == "acme.jobs.example-ats.com" and fetches[0]["result"] == "failed: address-refused"


def test_adapter_must_return_a_page_it_fetched(space_for, owner):
    forged = FetchedPage(url=ATS_URL, final_url=ATS_URL, content_type="text/html",
                         text="<main><h1>Anything</h1></main>", fetched_at="2026-11-15T10:00:00+00:00")
    with pytest.raises(FetchFailed) as e:
        capture_url(space_for(ExampleAts(return_page=forged)), owner, ATS_URL)
    assert e.value.reason == "adapter-error"


def test_a_failing_adapter_is_reported_and_audited(space_for, owner):
    space = space_for(ExampleAts(explode=True))
    with pytest.raises(FetchFailed) as e:
        capture_url(space, owner, ATS_URL)
    assert e.value.reason == "adapter-error" and "rb new-job --file" in str(e.value)
    entry = next(x for x in space.repo.audit_entries() if x["operation"] == "posting:adapter")
    assert entry["result"] == "failed: RuntimeError"
