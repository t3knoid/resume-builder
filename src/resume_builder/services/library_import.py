"""Import an existing resume into draft content library entries (issue #1, PRD R2.6–R2.11).

`resume_import.parse` reads the resume; this module turns what it found into library items:
every bullet, summary phrase, education and certification entry and skill group as a **draft**,
with new ids, never touching an existing entry. Likely duplicates of existing items are reported,
not merged. Nothing is reworded, and no metric, tag, date or title is filled in that the resume
doesn't state. The report says what was added, what was skipped and why, and what to check.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal

from urllib.parse import urlsplit

from pydantic import BaseModel, Field, ValidationError, model_validator

from .. import postingtext, resume_import
from ..errors import Conflict, ValidationFailed
from ..fetch import FetchFailed
from ..models import Bullet, Certification, Education, Project, Role, SkillGroup, SummaryPhrase, content_hash
from ..operations import operation
from ..security import RequestContext
from ..slug import is_slug, normalize_org_name, slugify
from ..space import UserSpace
from ..yamlio import MAX_FILE_BYTES
from .content import _save_library
from .orgs import SAME_THRESHOLD, find_matches

SAME_TEXT = 0.9   # bullets and phrases this similar to an existing one are reported as likely duplicates

ImportFormat = Literal["auto", "html", "markdown", "text"]


class ImportIn(BaseModel):
    text: str | None = Field(None, max_length=MAX_FILE_BYTES, description="The resume's text: HTML, Markdown or plain text")
    url: str | None = Field(None, max_length=2000, description="Or a link to the resume (a web page, a Markdown or text "
                                                                "file, a public Google Doc): fetched once, safely")
    format: ImportFormat = Field("auto", description="auto detects HTML and Markdown")
    name: str | None = Field(None, description="The file's name, if any: helps detect the format and is shown in the report")
    dry_run: bool = Field(True, description="Report what would be imported without changing anything. Set false to add the drafts.")
    expect_hash: str | None = Field(None, description="The content_hash a dry run returned: refuse if the resume "
                                                      "changed since (the CLI's preview, then import)")

    @model_validator(mode="after")
    def _one_source(self):
        if (self.text is None) == (self.url is None):
            raise ValueError("give the resume's `text` or its `url`, not both")
        return self


RESUME_FALLBACK = ("Instead, save your resume in your intake folder (open it in your browser and save the page, or copy "
                   "its text into intake/resume.txt) and run: rb import-resume intake/<file>")
_GOOGLE_DOC = re.compile(r"^https://docs\.google\.com/document/d/(?P<id>[A-Za-z0-9_-]{20,})(?:/[^?#]*)?(?:[?#].*)?$")


def resume_link(url: str) -> tuple[str, str | None]:
    """(link to fetch, note). A Google Docs share link opens an editor built with JavaScript, which the
    fetcher doesn't run; the document's own export link gives its HTML. A published doc (/d/e/...) is
    already a plain page."""
    m = _GOOGLE_DOC.match(url.strip())
    if m and m.group("id") != "e":
        return (f"https://docs.google.com/document/d/{m.group('id')}/export?format=html",
                "Read the Google Doc through its export link. It works only if the doc is shared with anyone who has the link.")
    return url.strip(), None


def fetch_resume(space: UserSpace, ctx: RequestContext, url: str) -> tuple[str, str, str | None, str | None]:
    """(text, format, name, note) of a resume behind a link: one safe GET with the user's intake limits
    (R13.5–R13.8), audited by host only. Sign-in pages and pages that need JavaScript are refused."""
    from .intake import fetch_posting

    if not re.match(r"^https?://", url.strip(), re.I):
        raise ValidationFailed(f"`{url[:80]}` isn't a web link. Use an http:// or https:// link, or a file in your intake folder.")
    target, note = resume_link(url)
    try:
        page = fetch_posting(space, ctx, target, audit_as="resume:fetch")
    except FetchFailed as exc:
        raise ValidationFailed(f"{exc.detail} {RESUME_FALLBACK}") from None
    name = urlsplit(page.final_url or target).path.rsplit("/", 1)[-1] or None
    if page.content_type == "text/html":
        c = postingtext.html_to_markdown(page.text)
        if c.has_password:
            raise ValidationFailed(f"The link opened a sign-in page, not your resume. {RESUME_FALLBACK}")
        if c.words < postingtext.MIN_WORDS and len(resume_import.html_to_lines(page.text).split()) < postingtext.MIN_WORDS:
            why = "shows its content only with JavaScript, which the app doesn't run" if c.needs_javascript else "has too little text to be a resume"
            raise ValidationFailed(f"The page {why}. {RESUME_FALLBACK}")
        return page.text, "html", name, note
    return page.text, "auto", name, note


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.replace("**", "").lower()))


def _similar(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a) and (a == b or SequenceMatcher(None, a, b).ratio() >= SAME_TEXT)


class _Ids:
    """New ids: readable, unique among current, archived and just-made ids (never reused, R7.10)."""

    def __init__(self, taken: set[str]):
        self.taken = set(taken)

    def make(self, *parts: str | None, words: int = 0) -> str:
        bits = []
        for p in parts:
            if not p:
                continue
            s = slugify(p)
            if words:
                s = "-".join(s.split("-")[:words])
            bits.append(s)
        base = "-".join(b for b in bits if b)[:60].strip("-") or "imported"
        if not is_slug(base):
            base = "imported"
        cand, n = base, 2
        while cand in self.taken:
            cand, n = f"{base}-{n}", n + 1
        self.taken.add(cand)
        return cand


def _short(text: str, n: int = 90) -> str:
    return text if len(text) <= n else text[: n - 3] + "..."


def import_resume(space: UserSpace, ctx: RequestContext, text: str, fmt: str = "auto", name: str | None = None,
                  dry_run: bool = True, source: str | None = None, notes: list[str] | None = None,
                  expect_hash: str | None = None) -> dict:
    digest = content_hash(text)
    if expect_hash and expect_hash != digest:
        raise Conflict("The resume changed since the preview, so nothing was imported. Run the import again to see "
                       "what it holds now.")
    lib = space.repo.load_library()
    reg = space.repo.load_registry()
    ids = _Ids(set(lib.all_ids()) | space.repo.archived_ids())

    def registry_company(company: str) -> object | None:
        matches = find_matches(reg.companies, company, SAME_THRESHOLD)
        return matches[0] if matches else None

    parsed = resume_import.parse(text, fmt, is_company=lambda n: registry_company(n) is not None, name=name)
    added: list[dict] = []
    duplicates: list[dict] = []
    skipped: list[dict] = []
    attention: list[str] = []
    companies: dict[str, dict] = {}
    with_numbers: list[str] = []
    existing_bullets = list(lib.all_bullets().values())

    def build(model, **data):
        try:
            return model.model_validate(data), None
        except ValidationError as exc:
            err = exc.errors()[0]
            where = ".".join(str(x) for x in err.get("loc", ()))
            return None, f"{where}: {err.get('msg', 'invalid')}" if where else err.get("msg", "invalid")

    def add_bullets(parent, prefix: str, texts: list[str], label: str) -> None:
        for t in texts:
            dup = next((b for b in existing_bullets if _similar(b.text, t)), None)
            if dup:
                duplicates.append({"kind": "bullet", "text": _short(t), "existing_id": dup.id})
                continue
            b, err = build(Bullet, id=ids.make(prefix, " ".join(t.split()[:5])),
                           text=t, tags=[], metric=None, evidence=None, limits=None, status="draft")
            if b is None:
                skipped.append({"kind": "bullet", "text": _short(t), "reason": err})
                continue
            parent.bullets.append(b)
            existing_bullets.append(b)
            added.append({"kind": "bullet", "id": b.id, "text": _short(t), "parent": parent.id, "under": label})
            if re.search(r"\d", t):
                with_numbers.append(b.id)

    # Summary phrases
    for sentence in parsed.summary:
        dup = next((s for s in lib.summary_phrases if _similar(s.text, sentence)), None)
        if dup:
            duplicates.append({"kind": "summary phrase", "text": _short(sentence), "existing_id": dup.id})
            continue
        p, err = build(SummaryPhrase, id=ids.make("summary", " ".join(sentence.split()[:4])), text=sentence, tags=[], status="draft")
        if p is None:
            skipped.append({"kind": "summary phrase", "text": _short(sentence), "reason": err})
            continue
        lib.summary_phrases.append(p)
        added.append({"kind": "summary phrase", "id": p.id, "text": _short(sentence)})

    # Roles and their bullets
    for e in parsed.roles:
        label = " | ".join(x for x in (e.title, e.company) if x) or e.header.split("\n")[0]
        missing = [f for f, v in (("title", e.title), ("company", e.company), ("start date", e.start)) if not v]
        if missing:
            skipped.append({"kind": "role", "text": _short(label),
                            "reason": f"No {', '.join(missing)} found on the resume ({len(e.bullets)} bullet(s) not imported). "
                                      "Add the role to base.yaml yourself, or fix the resume and import again."})
            continue
        known = registry_company(e.company)
        company = known.name if known else e.company
        same = next((r for r in lib.roles if normalize_org_name(r.company) == normalize_org_name(company) and r.start == e.start), None)
        if same:
            role = same
            attention.append(f"`{same.id}` ({same.title}, {same.company}) is already in your library: new bullets from "
                             f'"{_short(label, 60)}" were added to it as drafts; the role itself was left as it is.')
        else:
            role, err = build(Role, id=ids.make(company, e.title), title=e.title, official_title=None, company=company,
                              location=e.location, start=e.start, end=e.end or "present", description=None, bullets=[])
            if role is None:
                skipped.append({"kind": "role", "text": _short(label), "reason": err})
                continue
            lib.roles.append(role)
            if not known:   # a past employer: in the registry only if the user will track jobs there (R11.5)
                companies.setdefault(company, {"name": company})
            added.append({"kind": "role", "id": role.id, "text": _short(f"{role.title}, {role.company} ({role.start} – {role.end})")})
        for note in e.notes:
            attention.append(f"`{role.id}`: {note}")
        if company != e.company:
            attention.append(f'`{role.id}`: the resume says "{e.company}"; your registry calls it "{company}", so that name is used.')
        add_bullets(role, slugify(company).split("-")[0] if slugify(company) else "role", e.bullets, role.id)

    # Projects
    for e in parsed.projects:
        if not e.name:
            skipped.append({"kind": "project", "text": _short(e.header), "reason": "No project name found."})
            continue
        same = next((p for p in lib.projects if _norm(p.name) == _norm(e.name)), None)
        if same:
            project = same
            attention.append(f"`{same.id}` ({same.name}) is already in your library: new bullets were added to it as drafts.")
        else:
            project, err = build(Project, id=ids.make("project", e.name), name=e.name, start=e.start, end=e.end,
                                 description=e.description, bullets=[])
            if project is None:
                skipped.append({"kind": "project", "text": _short(e.name), "reason": err})
                continue
            lib.projects.append(project)
            added.append({"kind": "project", "id": project.id, "text": _short(e.name)})
            if not e.start:
                attention.append(f"`{project.id}`: no dates found. base.yaml asks for dates on projects; add them if you know them.")
        add_bullets(project, slugify(e.name).split("-")[0] or "project", e.bullets, project.id)

    # Education
    for ed in parsed.education:
        if not ed.institution:
            skipped.append({"kind": "education", "text": _short(ed.text), "reason": "No school found. Add it to base.yaml yourself."})
            continue
        dup = next((x for x in lib.education if _norm(x.institution) == _norm(ed.institution)
                    and _norm(x.degree or "") == _norm(ed.degree or "")), None)
        if dup:
            duplicates.append({"kind": "education", "text": _short(ed.text), "existing_id": dup.id})
            continue
        item, err = build(Education, id=ids.make("edu", ed.institution, ed.degree), institution=ed.institution,
                          degree=ed.degree, field=ed.field, start=ed.start, end=ed.end, status="draft")
        if item is None:
            skipped.append({"kind": "education", "text": _short(ed.text), "reason": err})
            continue
        lib.education.append(item)
        added.append({"kind": "education", "id": item.id, "text": _short(ed.text)})
        for note in ed.notes:
            attention.append(f"`{item.id}`: {note}")

    # Certifications
    for c in parsed.certifications:
        if not c.name:
            continue
        dup = next((x for x in lib.certifications if _norm(x.name) == _norm(c.name)), None)
        if dup:
            duplicates.append({"kind": "certification", "text": _short(c.text), "existing_id": dup.id})
            continue
        item, err = build(Certification, id=ids.make("cert", c.name), name=c.name, issuer=c.issuer, date=c.date, status="draft")
        if item is None:
            skipped.append({"kind": "certification", "text": _short(c.text), "reason": err})
            continue
        lib.certifications.append(item)
        added.append({"kind": "certification", "id": item.id, "text": _short(c.text)})

    # Skill groups
    for s in parsed.skills:
        group_name = s.name or "Skills"
        dup = next((g for g in lib.skills if _norm(g.name) == _norm(group_name)), None)
        if dup:
            new_items = [i for i in s.items if _norm(i) not in {_norm(x) for x in dup.items}]
            duplicates.append({"kind": "skill group", "text": _short(f"{group_name}: {', '.join(s.items)}"), "existing_id": dup.id})
            if new_items:
                attention.append(f"`{dup.id}` ({dup.name}) already exists, so it wasn't changed. Not in it yet: "
                                 f"{', '.join(new_items)}. Add the ones that are accurate yourself.")
            continue
        item, err = build(SkillGroup, id=ids.make("skills", group_name), name=group_name, items=s.items, status="draft")
        if item is None:
            skipped.append({"kind": "skill group", "text": _short(group_name), "reason": err})
            continue
        lib.skills.append(item)
        added.append({"kind": "skill group", "id": item.id, "text": _short(f"{group_name}: {', '.join(s.items)}")})

    # What the user should know
    attention += notes or []
    if parsed.contact_dropped:
        attention.append("Contact details (email, phone) were left out: they never go in base.yaml. They come from the "
                         "secrets named in user.yaml (see the user manual, contact details).")
    if parsed.links:
        attention.append("Links found, not imported: " + ", ".join(parsed.links) + ". Add the ones you want on your "
                         "resume under `links` in user.yaml.")
    for section in parsed.skipped_sections:
        attention.append(f'The "{section}" section isn\'t part of the content library, so it wasn\'t imported.')
    if parsed.unplaced:
        attention.append(f"{len(parsed.unplaced)} line(s) couldn't be placed under a role, project or section and were "
                         "left out: " + "; ".join(_short(u, 60) for u in parsed.unplaced[:5]) + ("…" if len(parsed.unplaced) > 5 else ""))
    if with_numbers:
        attention.append("These bullets mention numbers: " + ", ".join(with_numbers) + ". `metric` was left empty. If a number "
                         "is a result you can stand behind, copy it into `metric`; never estimate one.")
    if any(a["kind"] in ("bullet", "summary phrase") for a in added):
        attention.append("Imported bullets and summary phrases have no tags yet. Add tags (lowercase-with-hyphens) so "
                         "tailored profiles can use them as keywords.")

    suggested = [{"name": c["name"], "command": f'rb add-company "{c["name"]}"'} for c in companies.values()]
    applied = False
    if not dry_run and added:
        _save_library(space, ctx, lib)
        applied = True
    counts: dict[str, int] = {}
    for a in added:
        counts[a["kind"]] = counts.get(a["kind"], 0) + 1
    return {
        "dry_run": dry_run,
        "applied": applied,
        "format": resume_import.detect_format(text, name) if fmt == "auto" else fmt,
        "source": source,
        "content_hash": digest,
        "counts": counts,
        "added": added,
        "duplicates": duplicates,
        "skipped": skipped,
        "attention": attention,
        "companies_not_in_registry": suggested,
        "next": ("Nothing new to import." if not added else
                 ("Run it again without the dry run to add these as drafts." if dry_run else
                  "Review the new drafts in base.yaml: fix anything misread, add tags and metrics you can back up. "
                  "Then confirm them: `rb confirm --all` (or `rb confirm <id>`). Drafts never reach a real resume.")),
    }


@operation("library_import", "content:propose", ImportIn,
           "Import an existing resume (HTML, Markdown or plain text) as draft content library entries. Dry run by default.",
           writes=True)
def library_import(space: UserSpace, ctx: RequestContext, data: ImportIn) -> dict:
    if data.url is not None:
        text, fmt, name, note = fetch_resume(space, ctx, data.url)
        return import_resume(space, ctx, text, data.format if data.format != "auto" else fmt, data.name or name,
                             data.dry_run, source=data.url.strip(), notes=[note] if note else None,
                             expect_hash=data.expect_hash)
    return import_resume(space, ctx, data.text, data.format, data.name, data.dry_run, expect_hash=data.expect_hash)
