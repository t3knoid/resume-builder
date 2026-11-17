"""Choosing between resume templates (issue #16): the installed list, user.yaml and per-render choices,
every template and variant rendering for the example user, and exact PDF regeneration per template."""

from pathlib import Path

import pytest

from resume_builder import sync as sync_mod
from resume_builder.errors import PermissionDenied, ValidationFailed
from resume_builder.operations import REGISTRY, invoke
from resume_builder.security import DenyAllPolicy, RequestContext
from resume_builder.services.render import available_templates, export, load_snapshot, render_html

from .conftest import JOB, USER, data_file

TEMPLATES = ["classic", "modern", "traditional"]


def _render(sample, owner, **choice):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "export": None, **choice})["snapshot_id"]
    content, meta = load_snapshot(sample, sid)
    return sid, content, render_html(sample, content, meta)[0]


def test_templates_list_shows_every_installed_template_and_the_default(sample, owner):
    out = invoke(sample.app, owner, "templates_list", {})
    names = [t["name"] for t in out["templates"]]
    assert names == TEMPLATES
    assert next(t for t in out["templates"] if t["name"] == "classic")["versions"] == [1, 2, 3]
    assert next(t for t in out["templates"] if t["name"] == "modern") == {
        "name": "modern", "versions": [1], "newest": 1, "letter_template": "classic"}
    assert out["default"] == {"template": "classic", "template_version": None}


def test_templates_list_permission(sample, agent):
    assert REGISTRY["templates_list"].permission == "content:read" and not REGISTRY["templates_list"].writes
    viewer = RequestContext(actor="viewer", roles=("viewer",), user_id=USER)
    assert invoke(sample.app, viewer, "templates_list", {})["templates"]
    assert invoke(sample.app, agent, "templates_list", {})["templates"]
    sample.app.policy = DenyAllPolicy()
    with pytest.raises(PermissionDenied):
        invoke(sample.app, viewer, "templates_list", {})


@pytest.mark.parametrize("name", TEMPLATES)
@pytest.mark.parametrize("variant", ["styled", "ats"])
def test_every_template_and_variant_renders_the_example_user(sample, owner, name, variant):
    _, content, html = _render(sample, owner, template=name, variant=variant)
    assert content["template"] == {"name": name, "version": available_templates(sample, "resume")[name][-1]}
    assert f"resume/{name} v" in Path(sample.app.settings.templates_dir, "resume", name,
                                      str(content["template"]["version"]), "style.css").read_text(encoding="utf-8")
    for text in ("Alex Morgan", "Senior Platform Engineer", "Fabrikam Logistics", "alex@example.com"):
        assert text in html
    if variant == "ats":
        assert '<a href="https://github.com/example-alex">https://github.com/example-alex</a>' in html
        assert "Email: alex@example.com" in html and "**" not in html
    else:
        assert '<a href="https://github.com/example-alex">github.com/example-alex</a>' in html


def test_user_yaml_template_is_the_default(sample, owner, roots):
    p = data_file(roots, "user.yaml")
    p.write_text(p.read_text().replace("template: classic", "template: traditional", 1))
    invoke(sample.app, owner, "sync", {})
    assert sample.user_settings.template == "traditional"
    _, content, html = _render(sample, owner)
    assert content["template"]["name"] == "traditional" and "resume/traditional v1" in html
    _, content, _ = _render(sample, owner, template="modern")  # a per-render choice wins
    assert content["template"]["name"] == "modern"


def test_unknown_template_in_user_yaml_fails_with_the_choices(sample, roots):
    p = data_file(roots, "user.yaml")
    p.write_text(p.read_text().replace("template: classic", "template: fancy", 1))
    r = next(r for r in sync_mod.run_sync(sample, RequestContext.system(USER)).results if r.rel == "user.yaml")
    assert r.result == "failed"
    problem = r.problems[0]
    assert problem.file == "user.yaml" and problem.field == "template" and problem.example == "classic"
    assert "no resume template `fancy`" in problem.message
    assert "classic (versions 1, 2, 3), modern (versions 1), traditional (versions 1)" in problem.message
    assert sample.user_settings.template == "classic"  # the last good state is kept


def test_unknown_template_version_in_user_yaml_fails(sample, owner):
    settings = sample.user_settings.model_dump(mode="json")
    settings["template_version"] = 9
    with pytest.raises(ValidationFailed) as exc:
        invoke(sample.app, owner, "settings_replace", {"settings": settings})
    assert exc.value.problems[0].field == "template_version"
    assert "no version 9" in exc.value.problems[0].message


def test_per_render_choice_is_validated(sample, owner):
    with pytest.raises(ValidationFailed) as exc:
        invoke(sample.app, owner, "render", {"job_key": JOB, "export": None, "template": "fancy"})
    assert "Installed: classic" in exc.value.message
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "render", {"job_key": JOB, "export": None, "template": "modern", "template_version": 2})
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "render", {"job_key": JOB, "export": None, "template_version": 2})
    _, content, _ = _render(sample, owner, template="classic", template_version=2)
    assert content["template"] == {"name": "classic", "version": 2}


def test_letters_use_classic_when_the_template_has_no_letter_design(sample, owner):
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "doc_type": "answer", "slug": "why-here",
                                                 "export": None, "template": "modern"})["snapshot_id"]
    content, _ = load_snapshot(sample, sid)
    assert content["template"] == {"name": "classic", "version": 1}


def test_a_pinned_resume_version_does_not_break_letters(sample, owner):
    settings = sample.user_settings.model_dump(mode="json")
    settings["template_version"] = 2
    invoke(sample.app, owner, "settings_replace", {"settings": settings})
    sid = invoke(sample.app, owner, "render", {"job_key": JOB, "doc_type": "answer", "slug": "why-here",
                                                 "export": None})["snapshot_id"]
    assert load_snapshot(sample, sid)[0]["template"] == {"name": "classic", "version": 1}


@pytest.mark.parametrize("name", TEMPLATES)
def test_each_template_pdf_regenerates_byte_for_byte(sample, owner, name):
    pytest.importorskip("weasyprint")
    for variant in ("styled", "ats"):
        sid, _, _ = _render(sample, owner, template=name, variant=variant)
        first, _ = export(sample, sid, "pdf")
        a = Path(first).read_bytes()
        Path(first).unlink()
        second, _ = export(sample, sid, "pdf")
        assert a[:4] == b"%PDF" and a == Path(second).read_bytes()
