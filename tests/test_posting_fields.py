"""Application fields from a captured posting, and the passage check (PRD R13.13–R13.15).

Fixture pages are fictional employers only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from resume_builder import postingtext
from resume_builder.errors import ValidationFailed
from resume_builder.models import ExtractedField, PostingCapture
from resume_builder.posting_fields import check, extract, source_for_url, verify

FIXTURES = Path(__file__).parent / "fixtures" / "postings"


def html_capture(name: str, url: str = "https://careers.example.com/jobs/1") -> PostingCapture:
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    return PostingCapture(source_kind="url", source_url=url, content_type="text/html", raw_content=raw,
                          markdown=postingtext.convert(raw, "text/html"), captured_at="2026-11-15T10:00:00+00:00")


def text_capture(text: str) -> PostingCapture:
    return PostingCapture(source_kind="stdin", content_type="text/plain", raw_content=text,
                          markdown=postingtext.convert(text, "text/plain"), captured_at="2026-11-15T10:00:00+00:00")


def values(extraction) -> dict[str, str]:
    return {k: f.value for k, f in extraction.fields.items()}


# ---------------------------------------------------------------- extraction

def test_structured_data_comes_first():
    ex = extract(html_capture("structured.html"))
    v = values(ex)
    assert v["title"] == "Senior Site Reliability Engineer"
    assert v["company"] == "Fabrikam Inc."
    assert v["external_id"] == "FAB-20931"
    assert v["date_posted"] == "2026-11-10"          # structured data wins over the text's 2026-11-09 ...
    assert any("date_posted" in d and "2026-11-09" in d for d in ex.disagreements)  # ... and the difference is shown
    assert v["closing_date"] == "2026-12-31"
    assert v["location"] == "Austin, TX, US"
    assert v["salary_range"] == "$150,000 – $180,000 per year"
    assert not any(d.startswith("salary_range") for d in ex.disagreements)  # same amounts in the text
    assert v["work_arrangement"] == "remote"
    assert v["posting_url"] == "https://careers.example.com/jobs/1" and v["source"] == "company_site"
    assert ex.fields["title"].origin == "structured" and ex.fields["source"].origin == "rule"


def test_placeholder_closing_date_in_structured_data_is_not_used():
    """A site that writes the posting date + 100 years as validThrough: the page's "Posted until" wins."""
    ex = extract(html_capture("placeholder-closing.html"))
    v = values(ex)
    assert v["closing_date"] == "2026-11-18"
    assert ex.fields["closing_date"].origin == "rule"
    assert v["date_posted"] == "2026-11-12"  # "Posted until" isn't read as the posting date
    assert any(d.startswith("closing_date") and "2126-11-12" in d and "placeholder" in d for d in ex.disagreements)
    assert not any(d.startswith("date_posted") for d in ex.disagreements)


@pytest.mark.parametrize("valid_through, kept", [
    ("2026-12-31", True),     # a normal deadline
    ("2028-11-01", True),     # long, but within two years
    ("2029-01-01", False),    # more than two years after posting
    ("2026-11-01", False),    # before it was posted
])
def test_structured_closing_date_must_be_plausible(valid_through, kept):
    raw = ('<html><head><script type="application/ld+json">{"@context": "https://schema.org", "@type": "JobPosting", '
           f'"title": "Analyst", "datePosted": "2026-11-10", "validThrough": "{valid_through}"}}</script></head>'
           '<body><h1>Analyst</h1></body></html>')
    capture = PostingCapture(source_kind="url", source_url="https://careers.example.com/jobs/1", content_type="text/html",
                             raw_content=raw, markdown="# Analyst\n\nReview reports and answer questions about them.\n",
                             captured_at="2026-11-15T10:00:00+00:00")
    ex = extract(capture)
    assert (values(ex).get("closing_date") == valid_through) is kept
    assert any(d.startswith("closing_date") for d in ex.disagreements) is not kept


def test_rules_only_page():
    v = values(extract(html_capture("typical.html")))
    assert v == {
        "title": "Platform Engineer",
        "external_id": "NW-4821",
        "date_posted": "2026-11-02",
        "closing_date": "2026-12-15",
        "salary_range": "$140,000 – $165,000",
        "location": "Remote, United States",
        "work_arrangement": "remote",
        "posting_url": "https://careers.example.com/jobs/1",
        "source": "company_site",
    }


def test_onsite_page_without_structured_data():
    v = values(extract(html_capture("heavy-nav.html", url="https://www.indeed.com/viewjob?jk=1")))
    assert v["title"] == "Data Analyst"
    assert v["work_arrangement"] == "onsite"
    assert v["salary_range"] == "$70,000 - $82,000 per year"
    assert v["source"] == "job_board"
    assert "company" not in v and "date_posted" not in v


def test_pasted_text_with_labels_on_their_own_lines():
    v = values(extract(text_capture((FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8"))))
    assert v["title"] == "SYSTEMS ADMINISTRATOR III"
    assert v["date_posted"] == "2026-11-12"
    assert v["location"] == "SPRINGFIELD-ALL DISTRICTS"
    assert v["department"] == "INFORMATION TECHNOLOGY DIVISION"
    assert v["salary_range"] == "$98,500.00 – $141,200.00"
    assert v["external_id"] == "714052"
    assert v["posting_url"].startswith("https://jobs.springfield.example.gov/job/systems-administrator-iii")
    assert v["source"] == "company_site"
    assert "work_arrangement" not in v and "closing_date" not in v and "company" not in v


def test_nothing_is_invented_for_missing_fields():
    v = values(extract(text_capture("Platform Engineer\n\nWe build internal tools for developers. Requirements are listed below.\n")))
    assert v == {"title": "Platform Engineer"}


def test_mixed_arrangements_stay_unknown():
    ex = extract(text_capture("Platform Engineer\n\nThis is a hybrid role in Denver. Fully remote candidates in Colorado are also considered.\n"))
    assert "work_arrangement" not in ex.fields
    assert any(d.startswith("work_arrangement") for d in ex.disagreements)


def test_words_that_only_look_like_labels_are_ignored():
    v = values(extract(text_capture("Platform Engineer\n\nExperience with remote access tools is required for this job. "
                                    "Requisition approvals happen weekly.\n")))
    assert "external_id" not in v and "work_arrangement" not in v


def test_every_extracted_value_passes_the_check():
    captures = [html_capture(n) for n in ("structured.html", "typical.html", "heavy-nav.html", "js-shell-jsonld.html")]
    captures.append(text_capture((FIXTURES / "pasted-civil-service.txt").read_text(encoding="utf-8")))
    for c in captures:
        assert verify(c, extract(c).as_list()) == []


# ---------------------------------------------------------------- the passage check (any origin)

def field(name, value, passage, origin="agent") -> ExtractedField:
    return ExtractedField(name=name, value=value, passage=passage, origin=origin)


def test_a_quoted_value_is_accepted():
    cap = html_capture("typical.html")
    assert verify(cap, [field("location", "Remote, United States", "Location | Remote, United States"),
                        field("salary_range", "$140,000 – $165,000", "$140,000 &ndash; $165,000"),
                        field("work_arrangement", "remote", "Remote (US) · Full-time"),
                        field("date_posted", "2026-11-02", "Posted on 2026-11-02")]) == []


def test_a_passage_not_in_the_posting_is_rejected():
    cap = html_capture("typical.html")
    problems = verify(cap, [field("salary_range", "$200,000 – $250,000", "Salary: $200,000 – $250,000")])
    assert len(problems) == 1 and problems[0].field == "salary_range" and "isn't in the posting" in problems[0].message
    with pytest.raises(ValidationFailed):
        check(cap, [field("title", "Staff Engineer", "Staff Engineer")])


@pytest.mark.parametrize("name,value,passage", [
    ("date_posted", "2026-11-03", "Posted on 2026-11-02"),          # real passage, different date
    ("salary_range", "$140,000 – $185,000", "Salary range | $140,000 – $165,000"),
    ("location", "Austin, TX", "Location | Remote, United States"),
    ("work_arrangement", "hybrid", "Remote (US) · Full-time"),
    ("title", "Senior Platform Engineer", "Platform Engineer"),
    ("source", "linkedin", "https://careers.example.com/jobs/1"),
    ("date_posted", "November 2", "Posted on 2026-11-02"),          # not YYYY-MM-DD
])
def test_a_real_passage_that_doesnt_state_the_value_is_rejected(name, value, passage):
    problems = verify(html_capture("typical.html"), [field(name, value, passage)])
    assert len(problems) == 1 and "isn't supported" in problems[0].message


def test_structured_values_are_checked_against_the_embedded_data():
    cap = html_capture("structured.html")
    assert verify(cap, [field("company", "Fabrikam Inc.", "Fabrikam Inc.")]) == []
    assert verify(cap, [field("external_id", "FAB-20931", '"value": "FAB-20931"')]) == []


def test_a_field_given_twice_is_rejected():
    cap = html_capture("typical.html")
    problems = verify(cap, [field("title", "Platform Engineer", "Platform Engineer")] * 2)
    assert [p.field for p in problems] == ["title"]


def test_field_names_and_empty_values_are_refused():
    with pytest.raises(ValidationError):
        ExtractedField(name="interest_notes", value="I love it", passage="x")
    with pytest.raises(ValidationError):
        ExtractedField(name="title", value="  ", passage="x")


@pytest.mark.parametrize("url,source", [
    ("https://www.linkedin.com/jobs/view/1", "linkedin"),
    ("https://www.indeed.com/viewjob?jk=1", "job_board"),
    ("https://www.usajobs.gov/job/1", "job_board"),
    ("https://boards.greenhouse.io/acme/jobs/1", "company_site"),
    ("https://careers.example.com/1", "company_site"),
    ("not a url", None),
])
def test_source_for_url(url, source):
    assert source_for_url(url) == source
