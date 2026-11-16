"""Importing an existing resume into draft content library entries (issue #1, PRD R2.6–R2.10).

The fixtures in tests/fixtures/resumes/ are one fictional resume of the example user in three
formats. It overlaps the example library on purpose: one role, one bullet, a summary phrase, the
degree and a skill group are already there.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pydantic import ValidationError as InputError

from resume_builder import resume_import
from resume_builder.cli import main
from resume_builder.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from resume_builder.models import Profile
from resume_builder.operations import invoke
from resume_builder.security import RequestContext
from resume_builder.services.render import resolve_resume
from resume_builder.validation import validate_profile

from .conftest import USER, data_file

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "resumes"
FORMATS = ("resume.md", "resume.txt", "resume.html")


def text_of(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def run(sample, ctx, name: str = "resume.md", dry_run: bool = False) -> dict:
    return invoke(sample.app, ctx, "library_import", {"text": text_of(name), "name": name, "dry_run": dry_run})


# ---------------------------------------------------------------- reading the resume

@pytest.mark.parametrize("name", FORMATS)
def test_every_format_reads_the_same(name):
    assert_reads_the_fixture(resume_import.parse(text_of(name), name=name))


def assert_reads_the_fixture(p) -> None:
    """The fictional resume in tests/fixtures/resumes/, whatever format it was read from."""
    assert [(r.title, r.company, r.location, r.start, r.end, len(r.bullets)) for r in p.roles] == [
        ("Platform Engineer", "Northwind Laboratories", "Remote", "2024-07", "present", 2),
        ("Senior Platform Engineer", "Fabrikam Logistics", "Portland, OR", "2021-04", "2024-06", 2),
        ("Junior Systems Administrator", "Wide World Importers", "Seattle, WA", "2015", "2016", 1),
        ("Volunteer Mentor", "Code Club", None, None, None, 1),
    ]
    assert [(x.name, x.start, x.end) for x in p.projects] == [("Home lab", "2023", "present")]
    assert [(e.institution, e.degree, e.field, e.end) for e in p.education] == [
        ("Lakeside State University", "B.S.", "Computer Science", "2016")]
    assert [(c.name, c.issuer, c.date) for c in p.certifications] == [
        ("Certified Kubernetes Administrator (CKA)", "Cloud Native Computing Foundation", "2023")]
    assert [(s.name, s.items) for s in p.skills] == [("Automation", ["Ansible", "Python", "Bash", "Terraform"]),
                                                    ("Languages", ["Python", "Go"])]
    assert p.summary == ["Platform engineer who turns manual operations work into tested automation.",
                         "Calm in an incident and happy to write the runbook afterwards."]
    assert p.contact_dropped and p.links == ["https://www.linkedin.com/in/alex-morgan-example"]
    assert [s.lower() for s in p.skipped_sections] == ["awards"]


def test_bullets_keep_the_resumes_words():
    p = resume_import.parse(text_of("resume.html"), name="resume.html")
    assert p.roles[1].bullets[0] == ("Cut release time from 3 hours to 25 minutes by moving 14 services to GitHub Actions "
                                     "pipelines with automated tests.")      # bold dropped, words unchanged


@pytest.mark.parametrize("header, title, company", [
    ("Site Reliability Engineer at Contoso Health | 2019 – 2021", "Site Reliability Engineer", "Contoso Health"),
    ("Contoso Health — Site Reliability Engineer | 2019 – 2021", "Site Reliability Engineer", "Contoso Health"),
    ("Site Reliability Engineer, Contoso Health, Austin, TX, 2019 - 2021", "Site Reliability Engineer", "Contoso Health"),
])
def test_role_headers(header, title, company):
    e = resume_import.parse_role_header(header)
    assert (e.title, e.company, e.start, e.end) == (title, company, "2019", "2021")


def test_lines_after_the_dates_describe_the_role_and_are_not_read_as_it():
    e = resume_import.parse_role_header(
        "QA Manager\nNorthwind Labs | Springfield, IL | 2010 – 2016\n"
        "Held QA and support roles at several firms, most recently as QA Manager at Northwind Labs.")
    assert (e.title, e.company, e.location, e.start, e.end) == ("QA Manager", "Northwind Labs", "Springfield, IL", "2010", "2016")
    assert e.notes == ["Also on the header line, not imported: Held QA and support roles at several firms, "
                       "most recently as QA Manager at Northwind Labs.."]
    e = resume_import.parse_role_header("Platform Engineer | 2019 – 2021\nContoso Health")   # company after the dates
    assert (e.title, e.company) == ("Platform Engineer", "Contoso Health")


def test_a_description_before_marked_bullets_is_not_a_bullet():
    p = resume_import.parse("Experience\n\nQA Manager\nNorthwind Labs | 2010 – 2016\n"
                            "Northwind Labs builds scheduling software for clinics and hospitals across the whole region.\n"
                            "- Ran the release testing for every product.\n", "text")
    assert p.roles[0].bullets == ["Ran the release testing for every product."]
    assert "scheduling software" in p.roles[0].notes[-1]
    prose = resume_import.parse("Experience\n\nQA Manager\nNorthwind Labs | 2010 – 2016\n"
                                "Ran the release testing for every product we shipped in those six busy years there.\n", "text")
    assert len(prose.roles[0].bullets) == 1      # a prose-style resume: the line is the bullet


def test_an_unknown_heading_in_capitals_ends_the_skills():
    p = resume_import.parse("SKILLS\n\n- Automation: Ansible, Bash\n\nHOME LAB & SIDE PORTFOLIO\n\n"
                            "- Run a three-node cluster at home.\n\nEDUCATION\n\nBS in Computer Science, Example University\n", "text")
    assert [(s.name, s.items) for s in p.skills] == [("Automation", ["Ansible", "Bash"])]
    assert "HOME LAB & SIDE PORTFOLIO" in p.skipped_sections and p.education
    top = resume_import.parse("ALEX MORGAN\nalex@example.com\n\nEXPERIENCE\n\nEngineer | NORTHWIND LABS | 2019 – 2021\n"
                              "- Ran builds.\n", "text")
    assert not top.skipped_sections and top.roles[0].company == "NORTHWIND LABS"


def test_an_unclear_header_is_flagged_not_guessed_silently():
    e = resume_import.parse_role_header("Example Group | Quality Lead | 2020 – 2022")
    assert (e.title, e.company) == ("Quality Lead", "Example Group") and not e.notes
    e = resume_import.parse_role_header("Alpha | Beta | 2020 – 2022")
    assert e.notes and "Swap them" in e.notes[0]


@pytest.mark.parametrize("text, start, end", [
    ("Jan 2020 – Present", "2020-01", "present"),
    ("03/2019 - 12/2021", "2019-03", "2021-12"),
    ("2019-03 to 2021-05", "2019-03", "2021-05"),
    ("September 2018 - current", "2018-09", "present"),
    ("2017", "2017", "2017"),
])
def test_dates(text, start, end):
    assert resume_import.take_dates(text)[:2] == (start, end)


# ---------------------------------------------------------------- importing

def test_dry_run_changes_nothing(sample, owner, roots):
    before = data_file(roots, "base.yaml").read_text(encoding="utf-8")
    lib_before = sample.repo.load_library()
    out = run(sample, owner, dry_run=True)
    assert out["dry_run"] and not out["applied"] and out["added"]
    assert sample.repo.load_library() == lib_before
    assert data_file(roots, "base.yaml").read_text(encoding="utf-8") == before


@pytest.mark.parametrize("name", FORMATS)
def test_import_adds_drafts_and_never_touches_existing_items(sample, owner, roots, name):
    lib_before = sample.repo.load_library()
    out = run(sample, owner, name)
    assert out["applied"]
    lib = sample.repo.load_library()

    # Everything that existed is exactly as it was.
    for item_id, bullet in lib_before.all_bullets().items():
        assert lib.all_bullets()[item_id] == bullet
    for coll in ("summary_phrases", "education", "certifications", "skills"):
        assert getattr(lib, coll)[:len(getattr(lib_before, coll))] == getattr(lib_before, coll)
    fab = next(r for r in lib.roles if r.id == "role-fabrikam")
    old = next(r for r in lib_before.roles if r.id == "role-fabrikam")
    assert fab.model_dump(exclude={"bullets"}) == old.model_dump(exclude={"bullets"})

    # Every new item is a draft with a new id, no tags and no metric.
    added = {a["id"]: a for a in out["added"]}
    assert len(added) == len(out["added"])
    assert not set(added) & set(lib_before.all_ids())
    new_bullets = [b for b in lib.all_bullets().values() if b.id in added]
    assert new_bullets and all(b.status == "draft" and b.metric is None and b.tags == [] for b in new_bullets)
    for coll in (lib.summary_phrases, lib.education, lib.certifications, lib.skills):
        assert all(x.status == "draft" for x in coll if x.id in added)
    assert {a["kind"] for a in out["added"]} == {"summary phrase", "role", "bullet", "project", "certification", "skill group"}

    # Likely duplicates are reported, not imported or merged.
    dups = {d["existing_id"] for d in out["duplicates"]}
    assert {"fab-ci-pipeline", "sum-platform", "edu-bs", "sk-automation"} <= dups
    assert any("Terraform" in a and "sk-automation" in a for a in out["attention"])

    # A new bullet for a role already in the library goes under that role.
    assert [b.id for b in fab.bullets if b.id in added] and any(a.get("parent") == "role-fabrikam" for a in out["added"])

    # The registry's name is used for a company it knows by an alias; others are suggested, not created.
    northwind = next(r for r in lib.roles if r.id in added and r.title == "Platform Engineer")
    assert northwind.company == "Northwind Labs"
    assert [c["name"] for c in out["companies_not_in_registry"]] == ["Wide World Importers"]
    assert not any(c.name == "Wide World Importers" for c in sample.repo.load_registry().companies)

    # A role without dates isn't imported, and the report says why.
    assert any(s["kind"] == "role" and "Volunteer Mentor" in s["text"] and "start date" in s["reason"] for s in out["skipped"])


def test_contact_details_never_reach_the_library(sample, owner, roots):
    out = run(sample, owner, "resume.txt")
    base = data_file(roots, "base.yaml").read_text(encoding="utf-8")
    assert "alex@example.com" not in base and "555 010 0199" not in base and "linkedin.com" not in base
    assert any("Contact details" in a for a in out["attention"])
    assert any("linkedin.com" in a and "user.yaml" in a for a in out["attention"])
    assert any("Awards" in a or "AWARDS" in a for a in out["attention"])


def test_numbers_are_flagged_for_metrics_never_filled_in(sample, owner):
    out = run(sample, owner)
    bullets = {b.id: b for b in sample.repo.load_library().all_bullets().values()}
    flagged = next(a for a in out["attention"] if "mention numbers" in a)
    batch = next(a["id"] for a in out["added"] if a["kind"] == "bullet" and "30 nightly" in a["text"])
    assert batch in flagged and bullets[batch].metric is None


def test_write_back_keeps_base_yaml_comments(sample, owner, roots):
    path = data_file(roots, "base.yaml")
    path.write_text("# my own note\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    invoke(sample.app, owner, "sync", {})
    run(sample, owner)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# my own note") and "Moved 30 nightly batch jobs" in text and "status: draft" in text


def test_importing_twice_adds_nothing_new(sample, owner):
    run(sample, owner)
    again = run(sample, owner, "resume.html")
    assert again["added"] == [] and not again["applied"] and again["duplicates"]


def test_nothing_imported_renders_until_confirmed(sample, owner):
    out = run(sample, owner)
    lib = sample.repo.load_library()
    group = next(a["id"] for a in out["added"] if a["kind"] == "skill group")
    bullet = next(a for a in out["added"] if a["kind"] == "bullet" and a["parent"] == "role-fabrikam")
    profile = Profile(roles={"role-fabrikam": [bullet["id"]]}, skills=[group])
    about = [p for p in validate_profile(profile, lib, "profiles/public.yaml") if group in p.message or bullet["id"] in p.message]
    assert len(about) == 2 and all(p.severity == "warning" and "draft" in p.message for p in about)
    sections, used, drafts = resolve_resume(lib, profile, preview=False)
    assert sections["skills"] == [] and used == [] and set(drafts) == {group, bullet["id"]}

    preview = invoke(sample.app, owner, "library_confirm_all", {"dry_run": True})
    assert {group, bullet["id"]} <= {i["id"] for i in preview["items"]}
    invoke(sample.app, owner, "item_confirm", {"id": group})
    lib = sample.repo.load_library()
    sections, _, drafts = resolve_resume(lib, profile, preview=False)
    assert sections["skills"] and group not in drafts


def test_skill_groups_without_a_status_stay_confirmed(sample):
    assert all(g.status == "confirmed" for g in sample.repo.load_library().skills)


def test_the_brief_leaves_out_draft_skill_groups(sample, owner):
    out = run(sample, owner)
    group = next(a["id"] for a in out["added"] if a["kind"] == "skill group")
    brief = invoke(sample.app, owner, "agent_brief", {"job_id": "2026-11-02-northwind-labs-platform-engineering"})
    skills_part = brief.split("### Skill groups", 1)[1].split("###", 1)[0]
    assert group not in skills_part and group in brief.split("Not usable until the user confirms them", 1)[1]


# ---------------------------------------------------------------- permissions and interfaces

def test_the_agent_may_import_drafts_but_not_confirm_them(sample, agent):
    out = run(sample, agent)
    assert out["applied"]
    with pytest.raises(PermissionDenied):
        invoke(sample.app, agent, "item_confirm", {"id": out["added"][0]["id"]})


def test_viewer_cannot_import(sample):
    viewer = RequestContext(actor="viewer", roles=("viewer",), user_id=USER)
    with pytest.raises(PermissionDenied):
        invoke(sample.app, viewer, "library_import", {"text": "x"})
    with pytest.raises(PermissionDenied):
        invoke(sample.app, viewer, "library_import_file", {"file": "intake/resume.md"})


def test_import_from_the_intake_folder(sample, owner, roots):
    data_file(roots, "intake").mkdir(exist_ok=True)
    data_file(roots, "intake/resume.html").write_text(text_of("resume.html"), encoding="utf-8")
    out = invoke(sample.app, owner, "library_import_file", {"file": "intake/resume.html", "dry_run": True})
    assert out["file"] == "intake/resume.html" and out["format"] == "html" and out["added"]
    with pytest.raises(NotFound):
        invoke(sample.app, owner, "library_import_file", {"file": "intake/missing.md"})
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "library_import_file", {"file": "../base.yaml"})
    assert data_file(roots, "intake/resume.html").read_text(encoding="utf-8") == text_of("resume.html")   # never changed


@pytest.mark.parametrize("given", [
    f".\\data\\{USER}\\intake\\resume.html",                    # the host's path, from the repo folder (PowerShell)
    f"data/{USER}/intake/resume.html",
    f"C:\\Users\\someone\\resume-builder\\data\\{USER}\\intake\\resume.html",
    f"/home/someone/resume-builder/data/{USER}/intake/resume.html",
    "resume.html",
])
def test_a_host_path_to_the_intake_folder_works(sample, owner, roots, given):
    data_file(roots, "intake").mkdir(exist_ok=True)
    data_file(roots, "intake/resume.html").write_text(text_of("resume.html"), encoding="utf-8")
    out = invoke(sample.app, owner, "library_import_file", {"file": given, "dry_run": True})
    assert out["file"] == "intake/resume.html" and out["added"]


def test_a_path_to_another_users_intake_folder_is_not_read(sample, owner, roots):
    data_file(roots, "intake").mkdir(exist_ok=True)
    data_file(roots, "intake/resume.html").write_text(text_of("resume.html"), encoding="utf-8")
    with pytest.raises(NotFound):
        invoke(sample.app, owner, "library_import_file", {"file": "data/someone-else/intake/resume.html"})
    with pytest.raises(ValidationFailed):
        invoke(sample.app, owner, "library_import_file", {"file": f"data/{USER}/intake/../base.yaml"})


def test_bad_input_is_refused(sample, owner):
    with pytest.raises(InputError):
        invoke(sample.app, owner, "library_import", {"text": "x", "format": "pdf"})


def test_cli_import_resume(sample, roots, monkeypatch, capsys):
    data_file(roots, "intake").mkdir(exist_ok=True)
    data_file(roots, "intake/resume.md").write_text(text_of("resume.md"), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    code = main(["--user", USER, "import-resume", "intake/resume.md"])
    out = capsys.readouterr()
    assert code == 1 and "Not importing without an answer" in out.err + out.out
    assert main(["--user", USER, "import-resume", "intake/resume.md", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "New, as drafts" in out and "Added" in out and "rb confirm --all" in out
    assert "Moved 30 nightly batch jobs" in data_file(roots, "base.yaml").read_text(encoding="utf-8")


def test_cli_import_resume_from_stdin(sample, roots, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(text_of("resume.txt")))
    assert main(["--user", USER, "import-resume", "--stdin", "--yes"]) == 0
    assert "Added" in capsys.readouterr().out


# ---------------------------------------------------------------- from a link

class StubFetcher:
    """Pages by URL, with their content type. Records every URL asked for."""

    def __init__(self, pages: dict[str, tuple[str, str]]):
        self.pages, self.calls = pages, []

    def fetch(self, url, limits):
        from resume_builder.fetch import FetchedPage, FetchFailed

        self.calls.append(url)
        if url not in self.pages:
            raise FetchFailed("not-found", "The site answered 404 Not Found.")
        content_type, text = self.pages[url]
        return FetchedPage(url=url, final_url=url, content_type=content_type, text=text, fetched_at="2026-11-16T10:00:00+00:00")


PAGE = "https://alex.example.com/resume"
RAW_MD = "https://raw.example.com/alex/resume.md"
DOC_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
LOGIN = """<html><body><form><h1>Sign in</h1><p>Sign in to see this profile and much more.</p>
<input type="email"><input type="password"></form></body></html>"""


@pytest.fixture
def web(sample):
    stub = StubFetcher({
        PAGE: ("text/html", text_of("resume.html")),
        RAW_MD: ("text/plain", text_of("resume.md")),
        f"https://docs.google.com/document/d/{DOC_ID}/export?format=html": ("text/html", text_of("resume.html")),
        "https://social.example.com/in/alex": ("text/html", LOGIN),
        "https://app.example.com/resume": ("text/html", "<html><body><noscript>Please enable JavaScript.</noscript></body></html>"),
    })
    sample.app.fetcher = stub
    return stub


def link(sample, ctx, url, **extra):
    return invoke(sample.app, ctx, "library_import", {"url": url, **extra})


def fetch_audits(sample):
    return [e for e in sample.repo.audit_entries(1000) if e["operation"] == "resume:fetch"]


def test_import_from_a_web_page(sample, owner, web):
    out = link(sample, owner, PAGE, dry_run=False)
    assert out["applied"] and out["source"] == PAGE and out["format"] == "html"
    assert any(a["kind"] == "role" and "Wide World Importers" in a["text"] for a in out["added"])
    assert web.calls == [PAGE]
    audit = fetch_audits(sample)[0]
    assert audit["target"] == "alex.example.com" and audit["result"] == "ok"     # the host only, never the full link


def test_a_markdown_file_by_link_reads_like_the_file(sample, owner, web):
    by_link = link(sample, owner, RAW_MD)
    from_text = run(sample, owner, "resume.md", dry_run=True)
    assert by_link["format"] == "markdown"
    assert [a["text"] for a in by_link["added"]] == [a["text"] for a in from_text["added"]]


def test_a_google_docs_share_link_reads_the_export(sample, owner, web):
    out = link(sample, owner, f"https://docs.google.com/document/d/{DOC_ID}/edit?usp=sharing")
    assert web.calls == [f"https://docs.google.com/document/d/{DOC_ID}/export?format=html"]
    assert out["added"] and any("export link" in a for a in out["attention"])


@pytest.mark.parametrize("url, words", [
    ("https://social.example.com/in/alex", "sign-in page"),
    ("https://app.example.com/resume", "JavaScript"),
    ("https://alex.example.com/missing", "404"),
])
def test_pages_that_arent_a_resume_are_refused_with_the_intake_fallback(sample, owner, web, url, words):
    with pytest.raises(ValidationFailed) as e:
        link(sample, owner, url)
    assert words in e.value.message and "rb import-resume intake/" in e.value.message
    assert "new-job" not in e.value.message                     # not the job posting's fallback


def test_only_web_links(sample, owner, web):
    with pytest.raises(ValidationFailed):
        link(sample, owner, "file:///etc/passwd")
    assert web.calls == []


def test_fetching_can_be_turned_off(sample, owner, web):
    s = sample.user_settings
    sample.repo.put_settings(s.model_copy(update={"intake": s.intake.model_copy(update={"fetch": False})}), "owner")
    with pytest.raises(ValidationFailed) as e:
        link(sample, owner, PAGE)
    assert "intake.fetch: false" in e.value.message and web.calls == []
    assert fetch_audits(sample)[0]["result"].startswith("refused")


def test_text_or_url_not_both(sample, owner, web):
    with pytest.raises(InputError):
        invoke(sample.app, owner, "library_import", {"text": "x", "url": PAGE})
    with pytest.raises(InputError):
        invoke(sample.app, owner, "library_import", {})


def test_the_import_must_match_the_preview(sample, owner, web):
    preview = link(sample, owner, PAGE)
    web.pages[PAGE] = ("text/html", text_of("resume.html").replace("40 laptops", "45 laptops"))
    with pytest.raises(Conflict):
        link(sample, owner, PAGE, dry_run=False, expect_hash=preview["content_hash"])
    assert not any(a["id"] in sample.repo.load_library().all_ids() for a in preview["added"])


def test_the_agent_may_import_from_a_link_as_drafts(sample, agent, web):
    out = link(sample, agent, PAGE, dry_run=False)
    assert out["applied"] and fetch_audits(sample)[0]["actor"] == "agent"


def test_cli_import_resume_from_a_link(sample, roots, web, monkeypatch, capsys):
    from resume_builder.fetch import HttpPostingFetcher

    monkeypatch.setattr(HttpPostingFetcher, "fetch", lambda self, url, limits: web.fetch(url, limits))   # the CLI builds its own App
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert main(["--user", USER, "import-resume", PAGE, "--yes"]) == 0
    out = capsys.readouterr().out
    assert f"from {PAGE}" in out and "Added" in out
    assert web.calls == [PAGE, PAGE]                                 # the preview, then the import of the same page
    assert "Moved 30 nightly batch jobs" in data_file(roots, "base.yaml").read_text(encoding="utf-8")
