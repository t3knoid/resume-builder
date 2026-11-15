"""Job intake: matching the registry and finding duplicate applications (PRD R13.17, R13.18).

Uses the fictional example user: Northwind Labs (alias "Northwind Laboratories", department
Platform Engineering) and Contoso Health, and one job with posting link
https://example.com/jobs/1234 and posting id 1234.
"""

from __future__ import annotations

import pytest

from resume_builder.models import PostingCapture
from resume_builder.operations import invoke
from resume_builder.services.intake import (
    find_duplicates, match_company, match_department, normalize_posting_url,
)

from .conftest import JOB


# ---------------------------------------------------------------- companies

@pytest.mark.parametrize("name", ["Northwind Labs", "northwind labs", "Northwind Labs, Inc.", "NORTHWIND LABS LLC",
                                  "Northwind Laboratories", "northwind-labs"])
def test_same_company_is_suggested(sample, name):
    m = match_company(sample, name)
    assert m.suggested == "northwind-labs"
    assert m.candidates[0] == {"slug": "northwind-labs", "name": "Northwind Labs", "score": 1.0, "exact": True}


def test_near_miss_is_suggested_but_not_exact(sample):
    m = match_company(sample, "Northwind Lab")
    assert m.suggested == "northwind-labs" and m.candidates[0]["exact"] is False


def test_loose_match_is_offered_without_a_suggestion(sample):
    m = match_company(sample, "Contoso")
    assert m.suggested is None                          # not close enough to assume it's the same company
    assert [c["slug"] for c in m.candidates] == ["contoso-health"]
    assert m.new_slug == "contoso"


def test_unknown_company_offers_add_new_only(sample):
    m = match_company(sample, "Fabrikam Inc.")
    assert m.candidates == [] and m.suggested is None and m.new_slug == "fabrikam-inc"
    assert "fabrikam-inc" not in {c.slug for c in sample.repo.load_registry().companies}  # nothing created


def test_no_company_name(sample):
    assert match_company(sample, None).to_dict() == {"name": None, "candidates": [], "suggested": None, "new_slug": None}
    assert match_company(sample, "   ").name is None


def test_new_slug_never_reuses_a_taken_slug(sample):
    m = match_company(sample, "Northwind-Labs!")
    assert m.suggested == "northwind-labs" and m.new_slug is None


# ---------------------------------------------------------------- departments

def test_department_matches_within_its_company(sample):
    m = match_department(sample, "northwind-labs", "Platform Engineering Dept")
    assert m.candidates and m.candidates[0]["slug"] == "platform-engineering"
    assert match_department(sample, "northwind-labs", "platform engineering").suggested == "platform-engineering"


def test_department_of_another_or_unknown_company_is_new(sample):
    assert match_department(sample, "contoso-health", "Platform Engineering").candidates == []
    m = match_department(sample, None, "Information Technology Division")
    assert m.candidates == [] and m.new_slug == "information-technology-division"


# ---------------------------------------------------------------- duplicates

@pytest.mark.parametrize("a,b", [
    ("https://example.com/jobs/1234", "https://EXAMPLE.com/jobs/1234/"),
    ("https://example.com/jobs/1234", "https://www.example.com/jobs/1234#apply"),
    ("https://example.com/jobs/1234", "https://example.com/jobs/1234?utm_source=linkedin&trid=abc"),
    ("https://example.com/view?jk=9&b=2", "https://example.com/view?b=2&jk=9&ref=feed"),
])
def test_links_that_are_the_same_posting(a, b):
    assert normalize_posting_url(a) == normalize_posting_url(b)


@pytest.mark.parametrize("a,b", [
    ("https://example.com/jobs/1234", "https://example.com/jobs/1235"),
    ("https://example.com/view?jk=9", "https://example.com/view?jk=10"),     # the query names the posting
    ("https://example.com/jobs/1", "https://jobs.example.com/jobs/1"),
])
def test_links_that_are_different_postings(a, b):
    assert normalize_posting_url(a) != normalize_posting_url(b)


def test_same_link_is_a_duplicate(sample):
    dups = find_duplicates(sample, "https://www.example.com/jobs/1234?utm_campaign=x", None, None)
    assert [d["job_id"] for d in dups] == [JOB]
    assert dups[0]["reasons"] == ["same posting link"] and dups[0]["archived"] is False


def test_same_company_and_posting_id_is_a_duplicate(sample):
    dups = find_duplicates(sample, "https://careers.example.net/other", "northwind-labs", "#1234")
    assert [d["reasons"] for d in dups] == [["same company and posting id"]]
    assert find_duplicates(sample, None, "contoso-health", "1234") == []   # same id, other company
    assert find_duplicates(sample, None, None, "1234") == []               # no company: no id match


def test_both_reasons_are_listed(sample):
    dups = find_duplicates(sample, "https://example.com/jobs/1234", "northwind-labs", "1234")
    assert dups[0]["reasons"] == ["same posting link", "same company and posting id"]


def test_link_known_only_from_a_capture_is_a_duplicate(sample, owner):
    job = invoke(sample.app, owner, "job_new", {"company": "contoso-health"})["job_id"]
    sample.repo.add_posting_capture(PostingCapture(
        job_id=job, source_kind="url", source_url="https://careers.example.org/analyst?ref=email", content_type="text/plain",
        raw_content="Data Analyst", markdown="Data Analyst\n", captured_at="2026-11-15T10:00:00+00:00"), "owner")
    assert [d["job_id"] for d in find_duplicates(sample, "https://careers.example.org/analyst", None, None)] == [job]


def test_archived_applications_are_reported_as_archived(sample, owner):
    invoke(sample.app, owner, "job_archive", {"job_id": JOB})
    dups = find_duplicates(sample, "https://example.com/jobs/1234", None, None)
    assert dups and dups[0]["archived"] is True


def test_nothing_to_compare(sample):
    assert find_duplicates(sample, None, None, None) == []
    assert find_duplicates(sample, "https://careers.example.com/new", "northwind-labs", "9999") == []
