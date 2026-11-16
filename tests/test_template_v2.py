"""Resume template v2: earlier roles, company descriptions, visible links, bold markup."""

import pytest

from resume_builder.models import ContentLibrary, Profile
from resume_builder.operations import invoke
from resume_builder.services.render import (bare_url, build_content, linkify, load_snapshot, month_year,
                                            render_html, split_emphasis)
from resume_builder.validation import validate_library, validate_profile

from .conftest import JOB


# ---------------------------------------------------------------- helpers used by the template

def test_month_year():
    assert month_year("2022-07") == "Jul 2022"
    assert month_year("2016") == "2016"
    assert month_year("present") == "Present"
    assert month_year(None) == ""


def test_bare_url_drops_scheme_www_and_trailing_slash():
    assert bare_url("https://www.linkedin.com/in/someone/") == "linkedin.com/in/someone"
    assert bare_url("http://example.com") == "example.com"


def test_linkify_shows_the_address_and_escapes_text():
    html = str(linkify("Docs: https://lab.example.com/ <b>"))
    assert '<a href="https://lab.example.com/">lab.example.com</a>' in html
    assert "&lt;b&gt;" in html
    assert ">https://lab.example.com/<" in str(linkify("https://lab.example.com/", full=True))


def test_split_emphasis():
    assert split_emphasis("Built **Ansible** roles") == ("Built Ansible roles", [["Built ", False], ["Ansible", True], [" roles", False]])
    assert split_emphasis("no markers") == ("no markers", [["no markers", False]])
    assert split_emphasis("odd ** marker") == ("odd ** marker", [["odd ** marker", False]])


# ---------------------------------------------------------------- validation

def _profile(sample, **changes) -> Profile:
    base = sample.repo.get_profile(JOB).model_dump()
    base.update(changes)
    return Profile.model_validate(base)


def _errors(sample, profile):
    lib = sample.repo.load_library()
    return [p.message for p in validate_profile(profile, lib, "profile.yaml") if p.severity != "warning"]


def test_earlier_roles_count_as_shown(sample):
    profile = _profile(sample, roles={"role-fabrikam": ["fab-ci-pipeline"]}, earlier_roles={"role-tailspin": []})
    assert _errors(sample, profile) == []


def test_role_in_both_roles_and_earlier_roles_is_an_error(sample):
    profile = _profile(sample, earlier_roles={"role-tailspin": []})
    assert any("both `roles` and `earlier_roles`" in e for e in _errors(sample, profile))


def test_earlier_roles_must_be_older_than_main_roles(sample):
    profile = _profile(sample, roles={"role-tailspin": ["tail-docker"]}, earlier_roles={"role-fabrikam": []})
    assert any("must be older" in e for e in _errors(sample, profile))


def test_earlier_role_bullets_must_belong_to_that_role(sample):
    profile = _profile(sample, roles={"role-fabrikam": []}, earlier_roles={"role-tailspin": ["fab-ansible"]})
    assert any("belongs to a different role" in e for e in _errors(sample, profile))


def test_unclosed_bold_marker_is_reported(sample):
    lib = sample.repo.load_library()
    lib.roles[0].bullets[0].text = "Cut **release time"
    assert any("unclosed `**`" in p.message for p in validate_library(lib))


# ---------------------------------------------------------------- rendering

def _html(app, sample, owner, variant="styled", template_version=None, **profile_changes) -> str:
    if profile_changes:
        invoke(app, owner, "profile_write", {"key": JOB, "profile": _profile(sample, **profile_changes).model_dump()})
    sid = invoke(app, owner, "render", {"job_key": JOB, "variant": variant, "export": None})["snapshot_id"]
    content, meta = load_snapshot(sample, sid)
    if template_version:
        content["template"]["version"] = template_version
    html, _ = render_html(sample, content, meta)
    return html


def test_styled_layout(app, sample, owner):
    html = _html(app, sample, owner)
    assert html.index("Senior Platform Engineer</h3>") < html.index("Official title: Systems Engineer III") < html.index("Fabrikam Logistics</span>")
    assert "Fabrikam Logistics runs freight tracking software" in html
    assert "Apr 2021</time> – <time datetime=\"2024-06\">Jun 2024" in html
    assert '<a href="https://github.com/example-alex">github.com/example-alex</a>' in html
    assert html.index("Work Experience") < html.index("Core Skills") < html.index("Education")


def test_ats_layout_uses_full_urls_and_labels_contact(app, sample, owner):
    html = _html(app, sample, owner, variant="ats")
    assert '<a href="https://github.com/example-alex">https://github.com/example-alex</a>' in html
    assert "Email: alex@example.com" in html
    assert "<p>Official title: Systems Engineer III</p>" in html


def test_earlier_roles_render_as_one_condensed_entry(app, sample, owner):
    html = _html(app, sample, owner, roles={"role-fabrikam": ["fab-ci-pipeline"]}, earlier_roles={"role-tailspin": ["tail-docker"]})
    entry = html[html.index("Earlier Experience"):]
    assert "Tailspin Toys" in entry and "Jan 2017</time> – <time datetime=\"2021-03\">Mar 2021" in entry
    assert "<strong>Systems Administrator</strong>, Tailspin Toys" in entry


def test_bold_markup_is_bold_in_styled_and_plain_in_ats(app, sample, owner):
    lib = sample.repo.load_library()
    lib.roles[0].bullets[0].text = "Cut release time by moving builds to **GitHub Actions**."
    from resume_builder.services.content import _save_library
    _save_library(sample, owner, lib)
    assert "<strong>GitHub Actions</strong>" in _html(app, sample, owner)
    ats = _html(app, sample, owner, variant="ats")
    assert "moving builds to GitHub Actions." in ats and "**" not in ats


def test_template_v1_still_renders_new_snapshots(app, sample, owner):
    html = _html(app, sample, owner, template_version=1)
    assert "Senior Platform Engineer" in html and "**" not in html


# ---------------------------------------------------------------- earlier experience counts as on the resume

def test_bullet_ids_include_earlier_roles(sample):
    profile = _profile(sample, roles={"role-fabrikam": ["fab-ci-pipeline"]}, earlier_roles={"role-tailspin": ["tail-backup"]})
    assert {"fab-ci-pipeline", "lab-llm-server", "tail-backup"} <= profile.bullet_ids()


def test_letters_may_cite_bullets_under_earlier_roles(sample, agent):
    profile = _profile(sample, roles={"role-fabrikam": ["fab-ci-pipeline"]}, earlier_roles={"role-tailspin": ["tail-backup"]})
    invoke(sample.app, agent, "profile_write", {"key": JOB, "profile": profile.model_dump(mode="json")})
    out = invoke(sample.app, agent, "document_propose", {
        "job_id": JOB, "kind": "answer", "text": "I rebuilt the backups with weekly restore tests [cite: tail-backup]."})
    assert not any("tailored resume" in str(p) for p in out.get("warnings", []))
