"""Rendering from snapshots (PRD 8.5, 8.12).

Every document is first captured as a snapshot: the complete, resolved text plus the template
version, render settings, app version and image digest. HTML and PDF are always produced from a
snapshot alone, so they can be rebuilt exactly later. Rendered files are never stored.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import re

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape
from pydantic import BaseModel, Field

from .. import __version__
from ..clock import now_iso_precise
from ..errors import NotFound, Problem, ValidationFailed
from ..models import ContentLibrary, Profile, UserSettings
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace
from .documents import strip_citations

SNAPSHOT_SCHEMA = 1
_PDF_LOCK = threading.Lock()


# ---------------------------------------------------------------- contact details (R5.2, R12.3)

def contact_values(space: UserSpace, s: UserSettings | None = None) -> tuple[dict, list[str]]:
    """Contact details from the App's SecretStore, by the secret names in user.yaml."""
    s = s or space.user_settings
    names = {"email": s.contact.email_secret, "phone": s.contact.phone_secret}
    values, missing = {}, []
    for key, name in names.items():
        if not name:
            continue
        v = space.secrets.get(name, user_id=space.user)
        if v:
            values[key] = v
        else:
            missing.append(name)
    return values, missing


def contact_fingerprint(values: dict) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- templates

def template_roots(space: UserSpace) -> list[Path]:
    """Document template roots, searched in order: the App's extra roots, then the packaged one."""
    return [*space.app.extensions.document_template_dirs, space.app.settings.templates_dir]


def template_dir(space: UserSpace, family: str, name: str, version: int | None) -> tuple[Path, int]:
    """The folder of `<family>/<name>/<version>`, looking in the extra roots before the package.

    Extra roots can add templates and versions, but never replace a released packaged version:
    snapshots must regenerate byte for byte (R12.7).
    """
    packaged = space.app.settings.templates_dir
    found: dict[int, Path] = {}
    for root in template_roots(space):
        base = root / family / name
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if not (p.is_dir() and p.name.isdigit()):
                continue
            v = int(p.name)
            if root == packaged and v in found:
                raise ValidationFailed(
                    f"Template `{family}/{name}` version {v} is released and can't be replaced by {found[v]}. "
                    "Add a new version number instead.")
            found.setdefault(v, p)
    versions = sorted(found)
    if not versions:
        raise NotFound(f"No `{name}` template for {family}.")
    v = version or versions[-1]
    if v not in found:
        raise NotFound(f"Template `{name}` has no version {v}. Available: {versions}.")
    return found[v], v


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_URL = re.compile(r"https?://[^\s<>\"]+[^\s<>\".,;:!?)]")


def month_year(value: str | None) -> str:
    """`2022-07` -> `Jul 2022`, `2022` -> `2022`, `present` -> `Present`."""
    if not value:
        return ""
    if value == "present":
        return "Present"
    if len(value) == 7:
        return f"{_MONTHS[int(value[5:]) - 1]} {value[:4]}"
    return value


def bare_url(url: str) -> str:
    """The address as people read it: `https://www.github.com/x/` -> `github.com/x`."""
    out = re.sub(r"^https?://", "", url)
    out = re.sub(r"^www\.", "", out)
    return out.rstrip("/")


def linkify(text: str | None, full: bool = False) -> Markup:
    """Escape text and make each URL a link whose visible text is the address (bare, or full for ATS)."""
    if not text:
        return Markup("")
    out, last = [], 0
    for m in _URL.finditer(text):
        out.append(escape(text[last:m.start()]))
        url = m.group(0)
        out.append(Markup('<a href="{}">{}</a>').format(url, url if full else bare_url(url)))
        last = m.end()
    out.append(escape(text[last:]))
    return Markup("").join(out)


def split_emphasis(text: str) -> tuple[str, list[list]]:
    """`Built **Ansible** roles` -> plain text and [[text, bold], ...] parts. Unpaired `**` stays literal."""
    pieces = text.split("**")
    if len(pieces) % 2 == 0:  # unclosed marker: show it as written
        return text, [[text, False]]
    parts = [[p, i % 2 == 1] for i, p in enumerate(pieces) if p]
    return "".join(pieces), parts


def emphasis(parts: list) -> Markup:
    return Markup("").join(Markup("<strong>{}</strong>").format(t) if bold else escape(t) for t, bold in parts)


def _env(path: Path) -> Environment:
    # One template folder per version keeps a snapshot's output fixed; template_dir() picks the folder.
    env = Environment(loader=ChoiceLoader([FileSystemLoader(str(path))]), autoescape=select_autoescape(["html", "j2"]),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
    env.filters.update(month_year=month_year, bare_url=bare_url, linkify=linkify, emphasis=emphasis)
    return env


# ---------------------------------------------------------------- building snapshot content

def _person(s: UserSettings) -> dict:
    return {"name": s.display_name, "headline": s.headline, "location": s.location,
            "links": [l.model_dump() for l in s.links]}


def resolve_resume(lib: ContentLibrary, profile: Profile, preview: bool) -> tuple[dict, list[str], list[str]]:
    """Profile ids -> final text, in order. Returns (sections, bullet ids, drafts used)."""
    drafts: list[str] = []

    def ok(item) -> bool:
        if getattr(item, "status", "confirmed") != "confirmed":
            drafts.append(item.id)
            return preview
        return True

    phrases = {p.id: p for p in lib.summary_phrases}
    roles = {r.id: r for r in lib.roles}
    projects = {p.id: p for p in lib.projects}
    bullets = lib.all_bullets()
    used: list[str] = []

    def section(group: dict, table: dict, kind: str) -> list[dict]:
        out = []
        for pid, bids in group.items():
            item = table.get(pid)
            if item is None:
                raise ValidationFailed(f"The profile lists `{pid}`, which isn't in the content library.")
            chosen = []
            for bid in bids:
                b = bullets.get(bid)
                if b is None:
                    raise ValidationFailed(f"The profile lists bullet `{bid}`, which isn't in the content library.")
                if ok(b):
                    plain, parts = split_emphasis(b.text)
                    chosen.append({"id": b.id, "text": plain, "parts": parts})
                    used.append(b.id)
            entry = item.model_dump(mode="json", exclude={"bullets"})
            entry["bullets"] = chosen
            out.append(entry)
        return out

    summary = []
    for sid in profile.summary:
        p = phrases.get(sid)
        if p and ok(p):
            summary.append(p.text)
    edu = [e.model_dump(mode="json", exclude={"status", "id"}) for e in lib.education if e.id in profile.education and ok(e)]
    certs = [c.model_dump(mode="json", exclude={"status", "id"}) for c in lib.certifications if c.id in profile.certifications and ok(c)]
    groups = {g.id: g for g in lib.skills}
    skills = [{"name": groups[g].name, "items": groups[g].items} for g in profile.skills if g in groups]
    sections = {
        "summary": summary,
        "roles": section(profile.roles, roles, "role"),
        "earlier_roles": section(profile.earlier_roles, roles, "role"),
        "projects": section(profile.projects, projects, "project"),
        "education": edu,
        "certifications": certs,
        "skills": skills,
    }
    return sections, used, sorted(set(drafts))


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def build_content(space: UserSpace, job_key: str, doc_type: str, slug: str = "", preview: bool = False,
                  variant: str = "styled") -> tuple[dict, list[str]]:
    s = space.user_settings
    lib = space.repo.load_library()
    _, tversion = template_dir(space, "letter" if doc_type in ("cover_letter", "answer", "fit_report") else "resume",
                               s.template, s.template_version)
    contact, _ = contact_values(space, s)
    content: dict = {
        "schema": SNAPSHOT_SCHEMA,
        "doc_type": doc_type,
        "job_key": job_key,
        "preview": preview,
        "variant": variant,
        "template": {"name": s.template, "version": tversion},
        "settings": {"page_size": "Letter"},
        "person": _person(s),
        "contact": {"secrets": [n for n in (s.contact.email_secret, s.contact.phone_secret) if n],
                    "fingerprint": contact_fingerprint(contact)},
        "app_version": __version__,
        "image_digest": space.app.settings.image_digest,
    }
    bullet_ids: list[str] = []
    if doc_type in ("resume", "public_site"):
        key = "public" if doc_type == "public_site" else job_key
        profile = space.repo.get_profile(key)
        if profile is None:
            raise NotFound(f"No profile for `{key}`. " + ("Fill in profiles/public.yaml." if key == "public" else "Ask your AI agent to tailor one (`rb brief`)."))
        sections, bullet_ids, drafts = resolve_resume(lib, profile, preview)
        if drafts and not preview:
            raise ValidationFailed(
                "These items are still drafts, so only a preview can include them: " + ", ".join(drafts)
                + ". Confirm them in base.yaml (status: confirmed), or render with --preview.",
                [Problem(f"`{d}` is a draft.", file="base.yaml", field="status", example="confirmed") for d in drafts])
        content["sections"] = sections
        content["sources"] = {"bullet_ids": bullet_ids, "profile_hash": _hash(profile.model_dump(mode="json")),
                              "library_hash": _hash(lib.model_dump(mode="json"))}
        if job_key != "public":
            app = space.repo.get_application(job_key)
            reg = {c.slug: c for c in space.repo.load_registry().companies}
            comp = reg.get(app.company)
            content["sources"]["organization"] = {"company": app.company, "company_name": comp.name if comp else app.company,
                                                  "department": app.department}
    else:
        row = space.repo.get_document(job_key, doc_type, slug)
        if row is None:
            raise NotFound(f"No {doc_type.replace('_', ' ')} for `{job_key}` yet.")
        if preview is False and "[MOTIVATION NEEDED]" in row["text"]:
            raise ValidationFailed("Replace [MOTIVATION NEEDED] with your own words first.")
        app = space.repo.get_application(job_key)
        reg = {c.slug: c for c in space.repo.load_registry().companies}
        comp = reg.get(app.company)
        from .documents import split_sentences
        cited = sorted({i for _, ids in split_sentences(row["text"]) for i in ids})
        bullet_ids = cited
        if doc_type == "fit_report":  # notes keep their line breaks and lists
            paragraphs = [p.strip() for p in row["text"].split("\n\n") if p.strip()]
        else:
            paragraphs = [" ".join(p.split()) for p in strip_citations(row["text"]).split("\n\n") if p.strip()]
        content["document"] = {
            "question": row["question"],
            "paragraphs": paragraphs,
            "date": datetime.now(timezone.utc).date().isoformat(),
            "recipient": {"company": comp.name if comp else app.company, "title": app.title},
        }
        content["sources"] = {"bullet_ids": cited, "document_hash": _hash(row["text"])}
    return content, bullet_ids


def canonical(content: dict) -> str:
    return json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def create_snapshot(space: UserSpace, ctx: RequestContext, job_key: str, doc_type: str, slug: str = "",
                    reason: str = "draft", preview: bool = False, variant: str = "styled") -> str:
    content, bullet_ids = build_content(space, job_key, doc_type, slug, preview, variant)
    data = canonical(content)
    sid = "sn-" + hashlib.sha256(data.encode()).hexdigest()[:20]
    existing = space.repo.get_snapshot(sid)
    if existing:
        if reason in ("approved", "public") and existing["reason"] in ("draft", "preview"):
            with space.repo.transaction():
                space.repo.set_snapshot_reason(sid, reason)
        return sid
    from .snapshots import enforce_limits
    enforce_limits(space, ctx, job_key, doc_type)
    with space.repo.transaction():
        space.repo.insert_snapshot(sid, job_key, doc_type, preview, "preview" if preview else reason, data,
                                   bullet_ids, ctx.actor, now_iso_precise())
    space.audit(ctx, "snapshot:create", target=sid)
    return sid


# ---------------------------------------------------------------- rendering from a snapshot

def load_snapshot(space: UserSpace, sid: str) -> tuple[dict, dict]:
    row = space.repo.get_snapshot(sid)
    if row is None:
        raise NotFound(f"No snapshot `{sid}`.")
    return json.loads(row["data"]), dict(row)


def render_html(space: UserSpace, content: dict, meta: dict) -> tuple[str, list[str]]:
    """HTML from the snapshot alone; contact details from current secrets (R12.3)."""
    warnings = []
    s = space.user_settings
    contact, _ = contact_values(space, s)
    if contact_fingerprint(contact) != content["contact"]["fingerprint"]:
        warnings.append("Your contact details have changed since this document was created; the current ones are used.")
    if content.get("image_digest") != space.app.settings.image_digest:
        warnings.append(f"Created with image {content.get('image_digest')}; this is {space.app.settings.image_digest}. "
                        "Text and template are identical, but layout may shift slightly.")
    family = "letter" if content["doc_type"] in ("cover_letter", "answer", "fit_report") else "resume"
    tdir, _ = template_dir(space, family, content["template"]["name"], content["template"]["version"])
    env = _env(tdir)
    tpl = env.get_template("document.html.j2")
    css = (tdir / "style.css").read_text(encoding="utf-8")
    html = tpl.render(c=content, contact={"email": None, "phone": None, **contact}, created=meta["created_at"], css=css, sid=meta["id"])
    return html, warnings


def render_pdf(html: str, created_at: str, sid: str, base_url: str) -> bytes:
    """Deterministic PDF: metadata from the snapshot, fixed document identifier (R12.7)."""
    try:
        from weasyprint import HTML
    except ImportError as exc:  # pragma: no cover - only without the pdf extra
        raise ValidationFailed("PDF rendering needs WeasyPrint, which is included in the Docker image. "
                               "Run this command through `docker compose run --rm app ...`.") from exc
    # Embedded font subsets carry a "modified" timestamp. fontTools takes it from
    # SOURCE_DATE_EPOCH when set, so pin it to the snapshot's creation time.
    epoch = str(int(datetime.fromisoformat(created_at).timestamp()))
    with _PDF_LOCK:
        previous = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = epoch
        try:
            doc = HTML(string=html, base_url=base_url)
            return doc.write_pdf(pdf_identifier=sid.encode())
        finally:
            if previous is None:
                os.environ.pop("SOURCE_DATE_EPOCH", None)
            else:
                os.environ["SOURCE_DATE_EPOCH"] = previous


def file_name(space: UserSpace, content: dict, ext: str) -> str:
    name = "".join(ch for ch in content["person"]["name"].title() if ch.isalnum() or ch == " ").replace(" ", "_") or "Resume"
    kind = {"resume": "Resume", "public_site": "Resume", "cover_letter": "Cover_Letter", "answer": "Answer",
            "fit_report": "Fit_Report"}[content["doc_type"]]
    suffix = "" if content["job_key"] == "public" else f"_{content['job_key']}"
    variant = "_ATS" if content.get("variant") == "ats" else ""
    preview = "_PREVIEW" if content.get("preview") else ""
    return f"{name}_{kind}{suffix}{variant}{preview}.{ext}"


def export(space: UserSpace, sid: str, fmt: str = "pdf") -> tuple[str, list[str]]:
    """Render a snapshot and put the file in the output store. Returns (where it went, warnings)."""
    content, meta = load_snapshot(space, sid)
    html, warnings = render_html(space, content, meta)
    if content["doc_type"] == "answer" and fmt == "txt":
        name, data = file_name(space, content, "txt"), ("\n\n".join(content["document"]["paragraphs"]) + "\n").encode()
    elif fmt == "html":
        name, data = file_name(space, content, "html"), html.encode()
    else:
        tdir = space.app.settings.templates_dir
        name, data = file_name(space, content, "pdf"), render_pdf(html, meta["created_at"], sid, str(tdir))
    key = f"{content['job_key']}/{name}"
    try:
        where = space.output.put(key, data)
    except PermissionError as exc:
        rel = f"output/{space.user}/{key}"
        raise ValidationFailed(
            f"Can't write {rel}: the file is probably open in another program (a PDF viewer locks it on Windows). "
            f"Close it, then run `rb regenerate {sid}` to export it again. The snapshot is saved.") from exc
    return where, warnings


# ---------------------------------------------------------------- operations

class RenderIn(BaseModel):
    job_key: str = Field(description="A job id, or `public` for your public resume")
    doc_type: str = Field("resume", pattern="^(resume|cover_letter|answer|fit_report|public_site)$")
    slug: str | None = Field(None, description="For answers, e.g. why-here")
    preview: bool = Field(False, description="Include draft content, watermarked (R2.3)")
    variant: str = Field("styled", pattern="^(styled|ats)$")
    export: str | None = Field("pdf", pattern="^(pdf|html|txt)$", description="Also export a file to /output; null for none")


@operation("render", "resume:render", RenderIn, "Snapshot a document and (optionally) export it to /output.", writes=True)
def render_op(space: UserSpace, ctx: RequestContext, data: RenderIn) -> dict:
    slug = (data.slug or "why-here") if data.doc_type == "answer" else ""
    sid = create_snapshot(space, ctx, data.job_key, data.doc_type, slug=slug, preview=data.preview, variant=data.variant)
    out = {"snapshot_id": sid}
    if data.export:
        path, warnings = export(space, sid, data.export)
        out.update({"file": str(path), "warnings": warnings})
    return out


class RegenIn(BaseModel):
    snapshot_id: str | None = None
    job_key: str | None = None
    doc_type: str | None = None
    as_of: str | None = Field(None, description="ISO date or datetime: the latest snapshot at or before then (R12.8)")
    format: str = Field("pdf", pattern="^(pdf|html|txt)$")


@operation("regenerate", "snapshot:regenerate", RegenIn, "Rebuild a document exactly from its snapshot.")
def regenerate(space: UserSpace, ctx: RequestContext, data: RegenIn) -> dict:
    sid = data.snapshot_id
    if not sid:
        if not (data.job_key and data.doc_type and data.as_of):
            raise ValidationFailed("Give a snapshot id, or a job, document type and --as-of date.")
        from .snapshots import as_of
        sid = as_of(space, data.job_key, data.doc_type, data.as_of)
    path, warnings = export(space, sid, data.format)
    return {"snapshot_id": sid, "file": str(path), "warnings": warnings}


class Empty(BaseModel):
    pass


@operation("publish_public", "site:publish", Empty, "Render the public profile as a static site in /output/<user>/public/.", writes=True)
def publish_public(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    sid = create_snapshot(space, ctx, "public", "public_site", reason="public")
    with space.repo.transaction():
        space.repo.set_snapshot_reason(sid, "public")
    content, meta = load_snapshot(space, sid)
    html, warnings = render_html(space, content, meta)
    where = space.output.put("public/index.html", html.encode("utf-8"))
    s = space.user_settings
    note = ("Copy output/<user>/public/ to your publish target." if s.publish.target else
            "Set `publish.target` in user.yaml to record where it goes.")
    return {"snapshot_id": sid, "file": where, "warnings": warnings,
            "note": note + " Automatic pushing to the target is milestone 6 and not built yet."}
