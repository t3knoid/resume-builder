"""Job intake (PRD section 8.13): from a posting link or pasted text to a job, reviewed once."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from .. import posting_fields, postingtext
from ..adapters import MAX_FETCHES, AdapterCapture, adapter_for, host_matches
from ..clock import now_iso
from ..errors import Conflict, NotFound, Problem, ValidationFailed
from ..fetch import FetchedPage, FetchFailed, FetchLimits
from ..models import (
    Application, Company, Department, Event, ExtractedField, IntakeFieldName, PostingCapture, content_hash,
)
from ..operations import operation
from ..posting_fields import Extraction
from ..security import RequestContext
from ..space import UserSpace


def fetch_limits(space: UserSpace) -> FetchLimits:
    s = space.user_settings.intake
    return FetchLimits(timeout_seconds=s.timeout_seconds, max_bytes=s.max_bytes)


def fetch_posting(space: UserSpace, ctx: RequestContext, url: str) -> FetchedPage:
    """Fetch one posting URL with the user's limits, through the App's fetcher (R13.5–R13.9).

    Audited with the host only, never the full URL or the content (R13.27). Callers decide what to
    do with the page; nothing is stored here.
    """
    host = (urlsplit(url).hostname or "").lower() or "(invalid url)"
    if not space.user_settings.intake.fetch:
        space.audit(ctx, "posting:fetch", target=host, result="refused: intake.fetch is false")
        raise FetchFailed("fetch-disabled", "Fetching links is turned off (`intake.fetch: false` in user.yaml).")
    try:
        page = space.app.fetcher.fetch(url, fetch_limits(space))
    except FetchFailed as exc:
        space.audit(ctx, "posting:fetch", target=host, result=f"failed: {exc.reason}")
        raise
    except Exception as exc:
        space.audit(ctx, "posting:fetch", target=host, result=f"failed: {type(exc).__name__}")
        raise
    space.audit(ctx, "posting:fetch", target=host, result="ok")
    return page


# ---------------------------------------------------------------- captures and posting.md (R13.10–R13.12)

def capture_from_page(page: FetchedPage) -> PostingCapture:
    """A capture of a fetched page, converted to Markdown. Refuses pages that aren't a usable
    posting (an empty JavaScript shell, a sign-in page) with the paste fallback (R13.3)."""
    try:
        markdown = postingtext.convert(page.text, page.content_type)
    except postingtext.CaptureRefused as exc:
        raise FetchFailed(exc.reason, exc.message) from None
    return PostingCapture(source_kind="url", source_url=page.url, content_type=page.content_type,
                          raw_content=page.text, markdown=markdown, captured_at=page.fetched_at)


def capture_from_text(text: str, *, source_kind: str = "stdin", source_path: str | None = None,
                      source_url: str | None = None) -> PostingCapture:
    """A capture of pasted text (paste fallback, R13.2). The text is kept as is."""
    try:
        markdown = postingtext.convert(text, "text/plain")
    except postingtext.CaptureRefused as exc:
        raise ValidationFailed(f"{exc.message} Paste the full posting text.") from None
    return PostingCapture(source_kind=source_kind, source_path=source_path, source_url=source_url,
                          content_type="text/plain", raw_content=text, markdown=markdown, captured_at=now_iso())


def source_label(capture: PostingCapture) -> str:
    if capture.source_kind == "url":
        return capture.source_url or "a link"
    if capture.source_kind == "file":
        return f"Pasted text ({capture.source_path})" + (f", from {capture.source_url}" if capture.source_url else "")
    return "Pasted text" + (f", from {capture.source_url}" if capture.source_url else "")


def posting_edited_by_user(space: UserSpace, job_id: str) -> bool:
    """True when posting.md holds posting text that isn't the latest capture: text the user pasted
    or edited. Intake never replaces that without being asked (R13.12)."""
    row = space.repo.application_row(job_id)
    body = postingtext.body_of(row["posting_text"] if row else None)
    if not body:
        return False
    latest = space.repo.latest_posting_capture(job_id)
    return latest is None or body != latest.markdown.strip()


def _store_posting(space: UserSpace, ctx: RequestContext, job_id: str, capture: PostingCapture,
                   current_posting: str | None) -> PostingCapture:
    """Inside the caller's transaction: store or link the capture and set the job's posting text."""
    text = postingtext.posting_document(capture.markdown, source_label(capture), capture.captured_at[:10],
                                        notes=postingtext.notes_of(current_posting))
    if capture.id is None:
        capture = space.repo.add_posting_capture(capture.model_copy(update={"job_id": job_id}), ctx.actor)
    elif capture.job_id is None:
        space.repo.attach_posting_capture(capture.id, job_id)
        capture = capture.model_copy(update={"job_id": job_id})
    space.repo.set_posting(job_id, text)
    space.audit(ctx, "posting:capture", target=job_id, result=f"capture {capture.id}")
    return capture


def attach_posting(space: UserSpace, ctx: RequestContext, job_id: str, capture: PostingCapture,
                   replace: bool = False) -> dict:
    """Store `capture` for `job_id` (or link a stored one) and make it the job's posting.md.

    The posting part of posting.md is replaced; the user's "My notes" section is kept. Refuses when
    posting.md holds posting text the user wrote or edited, unless `replace` is set. If the file has
    edits that haven't synced yet, it is left alone and the next sync reports a conflict.
    """
    row = space.repo.application_row(job_id)
    if row is None:
        raise NotFound(f"No job `{job_id}`.")
    if capture.job_id not in (None, job_id):
        raise Conflict(f"That capture belongs to `{capture.job_id}`.")
    if not replace and posting_edited_by_user(space, job_id):
        raise Conflict(f"jobs/{job_id}/posting.md has posting text you pasted or edited, so intake won't replace it. "
                       "The new capture was not saved. Run it again with replace to use the captured text "
                       "(your \"My notes\" section is kept either way).")
    with space.repo.transaction():
        capture = _store_posting(space, ctx, job_id, capture, row["posting_text"])
    file_updated = space.changed("posting", job_id)
    return {"job_id": job_id, "capture_id": capture.id, "file_updated": file_updated,
            "note": None if file_updated else
            f"jobs/{job_id}/posting.md has edits that haven't synced, so it wasn't changed. Sync will show a conflict; "
            "run `rb resolve` to pick a version."}


# ---------------------------------------------------------------- capturing a URL, with or without a site adapter

@dataclass
class UrlCapture:
    capture: PostingCapture
    extraction: Extraction
    adapter: str | None = None
    notes: list[str] = field(default_factory=list)


def capture_url(space: UserSpace, ctx: RequestContext, url: str) -> UrlCapture:
    """Fetch a posting URL and turn it into a capture with extracted fields (nothing is stored).

    A site adapter registered for the URL's domain reads the page instead of the generic path
    (R13.16), under the same checks.
    """
    adapter = adapter_for(space.app.extensions.site_adapters, url)
    if adapter is not None:
        return _capture_with_adapter(space, ctx, url, adapter)
    capture = capture_from_page(fetch_posting(space, ctx, url))
    return UrlCapture(capture, posting_fields.extract(capture))


def _capture_with_adapter(space: UserSpace, ctx: RequestContext, url: str, adapter) -> UrlCapture:
    fetched: list[FetchedPage] = []

    def fetch(target: str) -> FetchedPage:
        host = (urlsplit(target).hostname or "").lower()
        if not any(host_matches(host, d) for d in adapter.domains):
            raise FetchFailed("adapter-domain", f"The site adapter `{adapter.name}` may only fetch from its own sites, not `{host or target}`.")
        if len(fetched) >= MAX_FETCHES:
            raise FetchFailed("adapter-limit", f"The site adapter `{adapter.name}` tried to make more than {MAX_FETCHES} requests.")
        page = fetch_posting(space, ctx, target)
        fetched.append(page)
        return page

    try:
        result = adapter.capture(url, fetch)
    except FetchFailed:
        raise
    except Exception as exc:
        space.audit(ctx, "posting:adapter", target=adapter.name, result=f"failed: {type(exc).__name__}")
        raise FetchFailed("adapter-error", f"The site adapter `{adapter.name}` couldn't read this page.") from exc
    if not isinstance(result, AdapterCapture) or not any(result.page is p for p in fetched):
        space.audit(ctx, "posting:adapter", target=adapter.name, result="failed: page not fetched through intake")
        raise FetchFailed("adapter-error", f"The site adapter `{adapter.name}` didn't return a page it fetched through intake.")

    page, notes = result.page, []
    markdown = None
    if result.markdown is not None:
        md = postingtext.text_to_markdown(result.markdown)
        if md.strip() and postingtext.wording_preserved(md, page.text, page.content_type):
            markdown = md
        else:
            notes.append(f"The site adapter `{adapter.name}` changed the posting's wording, so its text wasn't used; "
                         "the page was converted directly.")
    if markdown is None:
        capture = capture_from_page(page).model_copy(update={"source_url": url})
    else:
        capture = PostingCapture(source_kind="url", source_url=url, content_type=page.content_type,
                                 raw_content=page.text, markdown=markdown, captured_at=page.fetched_at)

    good: list[ExtractedField] = []
    for f in result.fields:
        f = f.model_copy(update={"origin": "adapter"})
        problems = posting_fields.verify(capture, [f])
        if problems:
            notes.append(f"The site adapter `{adapter.name}` gave a value for {f.name} that the posting doesn't state; it was ignored.")
        else:
            good.append(f)
    space.audit(ctx, "posting:adapter", target=adapter.name, result="ok")
    return UrlCapture(capture, posting_fields.extract(capture, first=good), adapter=adapter.name, notes=notes)


# ---------------------------------------------------------------- registry matching (R13.17)

@dataclass
class OrgMatch:
    """How a company or department name from a posting relates to the registry.

    `candidates` are existing entries, best first, each {slug, name, score, exact}. `suggested` is
    the slug intake offers by default: an existing entry that is the same organization (R11.4), or
    None when the user must choose. `new_slug` is the slug a new entry would get if the user
    chooses "add new"; nothing is created here (R11.5).
    """

    name: str | None
    candidates: list[dict] = field(default_factory=list)
    suggested: str | None = None
    new_slug: str | None = None

    def to_dict(self) -> dict:
        return {"name": self.name, "candidates": self.candidates, "suggested": self.suggested, "new_slug": self.new_slug}


def _org_match(entries, name: str | None) -> OrgMatch:
    from ..slug import is_slug, slugify
    from .orgs import SAME_THRESHOLD, SUGGEST_THRESHOLD, score_matches

    if not name or not name.strip():
        return OrgMatch(name=None)
    scored = score_matches(entries, name, threshold=SUGGEST_THRESHOLD)
    candidates = [{"slug": e.slug, "name": e.name, "score": round(s, 2), "exact": s == 1.0} for s, e in scored]
    suggested = candidates[0]["slug"] if candidates and scored[0][0] >= SAME_THRESHOLD else None
    slug = slugify(name)
    taken = {e.slug for e in entries}
    new_slug = slug if is_slug(slug) and slug not in taken else None
    return OrgMatch(name=name.strip(), candidates=candidates, suggested=suggested, new_slug=new_slug)


def match_company(space: UserSpace, name: str | None) -> OrgMatch:
    """Registry companies matching a company name from a posting, best first (R13.17)."""
    return _org_match(space.repo.load_registry().companies, name)


def match_department(space: UserSpace, company_slug: str | None, name: str | None) -> OrgMatch:
    """Departments of `company_slug` matching a department name from a posting. With no company
    (yet), there is nothing to match against: every name is new."""
    reg = space.repo.load_registry()
    company = next((c for c in reg.companies if c.slug == company_slug), None) if company_slug else None
    return _org_match(company.departments if company else [], name)


# ---------------------------------------------------------------- duplicate check (R13.18)

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "trid", "ref", "refid", "referrer", "src", "source", "gh_src",
                   "lever-origin", "lever-source", "trk", "trkinfo", "trackingid", "tracking_id", "sid", "_hsenc", "_hsmi"}


def normalize_posting_url(url: str | None) -> str | None:
    """A posting link reduced to what identifies the posting: lowercase scheme and host, no
    "www.", no fragment, no trailing slash, and no tracking parameters (utm_*, ref, trid, ...)."""
    from urllib.parse import parse_qsl, urlencode

    if not url or not url.strip():
        return None
    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.hostname:
        return url.strip().lower()
    host = parts.hostname.lower().removeprefix("www.")
    if parts.port and parts.port not in (80, 443):
        host += f":{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    q = urlencode(sorted(query))
    return f"{parts.scheme.lower()}://{host}{path}" + (f"?{q}" if q else "")


def _norm_external_id(value: str | None) -> str | None:
    if value is None:
        return None
    v = str(value).strip().lstrip("#").strip().lower()
    return v or None


def find_duplicates(space: UserSpace, posting_url: str | None, company_slug: str | None,
                    external_id: str | None) -> list[dict]:
    """Applications that look like the same posting: the same link (tracking parameters ignored), or
    the same company and external posting id. Archived applications count too, and say so."""
    url = normalize_posting_url(posting_url)
    ext = _norm_external_id(external_id)
    out = []
    for row in space.repo.list_application_rows(include_archived=True):
        data = json.loads(row["data"])
        reasons = []
        if url and (normalize_posting_url(data.get("posting_url")) == url or any(
                normalize_posting_url(c["source_url"]) == url for c in space.repo.list_posting_captures(row["job_id"]))):
            reasons.append("same posting link")
        if ext and company_slug and row["company_slug"] == company_slug and _norm_external_id(data.get("external_id")) == ext:
            reasons.append("same company and posting id")
        if reasons:
            out.append({"job_id": row["job_id"], "title": data.get("title"), "stage": row["stage"],
                        "archived": bool(row["archived"]), "reasons": reasons})
    out.sort(key=lambda d: (d["archived"], d["job_id"]), reverse=False)
    return out


# ---------------------------------------------------------------- operations (R13.19–R13.21, R13.25)

APPLICATION_FIELDS = ("title", "posting_url", "source", "external_id", "location", "work_arrangement",
                      "salary_range", "date_posted", "closing_date")


class IntakePreviewIn(BaseModel):
    url: str | None = Field(None, description="The posting's link. Fetched once, unless `text` is given.")
    text: str | None = Field(None, description="The posting text, pasted, when the link can't be fetched. "
                                                "`url` is then only recorded as the posting's link.")

    @model_validator(mode="after")
    def _one_source(self):
        if not (self.url and self.url.strip()) and not (self.text and self.text.strip()):
            raise ValueError("give the posting's link (`url`) or its text (`text`)")
        return self


@operation("job_intake_preview", "application:write", IntakePreviewIn,
           "Capture a posting from its link or text and show the job intake would create. Saves only the capture.",
           writes=True)
def job_intake_preview(space: UserSpace, ctx: RequestContext, data: IntakePreviewIn) -> dict:
    url = data.url.strip() if data.url and data.url.strip() else None
    if data.text and data.text.strip():
        return preview_text(space, ctx, data.text, source_kind="stdin", url=url)
    result = capture_url(space, ctx, url)
    capture = _keep_capture(space, ctx, result.capture)
    return _preview(space, capture, result.extraction, adapter=result.adapter, notes=result.notes)


def preview_text(space: UserSpace, ctx: RequestContext, text: str, *, source_kind: str,
                 source_path: str | None = None, url: str | None = None) -> dict:
    """The preview for pasted posting text (R13.2): piped in, or read from the user's intake
    folder by the file adapter (`job_intake_preview_file`)."""
    capture = capture_from_text(text, source_kind=source_kind, source_path=source_path,
                                source_url=url.strip() if url and url.strip() else None)
    extraction = posting_fields.extract(capture)
    capture = _keep_capture(space, ctx, capture)
    return _preview(space, capture, extraction)


def _keep_capture(space: UserSpace, ctx: RequestContext, capture: PostingCapture) -> PostingCapture:
    """Store the capture (no job yet), or reuse an identical one from an earlier preview."""
    same = space.repo.unattached_posting_capture(content_hash(capture.raw_content), capture.source_kind,
                                                  capture.source_url, capture.source_path)
    if same is not None:
        return same
    with space.repo.transaction():
        return space.repo.add_posting_capture(capture, ctx.actor)


def _preview(space: UserSpace, capture: PostingCapture, extraction: Extraction, adapter: str | None = None,
             notes: list[str] | None = None) -> dict:
    from ..slug import build_job_id
    from .jobs import today_for

    values = {k: f.value for k, f in extraction.fields.items()}
    company = match_company(space, values.get("company"))
    company_slug = company.suggested
    department = match_department(space, company_slug, values.get("department"))
    duplicates = find_duplicates(space, values.get("posting_url"), company_slug, values.get("external_id"))
    job_id = None
    slug_for_id = company_slug or company.new_slug
    if slug_for_id:
        job_id = build_job_id(today_for(space), slug_for_id, department.suggested or department.new_slug,
                              set(space.repo.job_ids()))
    return {
        "capture_id": capture.id,
        "source": source_label(capture),
        "source_path": capture.source_path,
        "adapter": adapter,
        "fields": [f.model_dump() for f in extraction.as_list()],
        "disagreements": extraction.disagreements,
        "notes": list(notes or []),
        "company": company.to_dict(),
        "department": department.to_dict(),
        "duplicates": duplicates,
        "job_id": job_id,
        "posting_preview": capture.markdown[:1500],
    }


class IntakeIn(BaseModel):
    capture_id: int = Field(description="From job_intake_preview")
    company: str | None = Field(None, description="Company slug, or a name. Default: the preview's suggestion.")
    department: str | None = Field(None, description="Department slug or name. Default: the preview's suggestion, if any.")
    create_missing: bool = Field(False, description="Add the company/department to the registry if none matches")
    fields: list[ExtractedField] | None = Field(None, description="The preview's fields. Each is checked against the "
                                                                  "posting again. Default: extracted again from the capture.")
    overrides: dict[IntakeFieldName, str | None] = Field(
        default_factory=dict, description="Your own values, replacing extracted ones (null clears one)")
    recapture: str | None = Field(None, description="A job id: save this posting for that existing job instead of creating one")
    replace_posting: bool = Field(False, description="With recapture: replace posting text you pasted or edited")
    allow_duplicate: bool = Field(False, description="Create a new job even though one looks like the same posting")


@operation("job_intake", "application:write", IntakeIn,
           "Create a job from a previewed posting: fields filled in, posting.md written, all in one step.", writes=True)
def job_intake(space: UserSpace, ctx: RequestContext, data: IntakeIn) -> dict:
    from ..slug import build_job_id
    from ..validation import validate_application, validate_registry
    from .jobs import today_for
    from .orgs import SAME_THRESHOLD, score_matches

    capture = space.repo.get_posting_capture(data.capture_id)
    if capture is None:
        raise NotFound(f"No posting capture {data.capture_id}. Run the preview again.")
    if capture.job_id is not None:
        raise Conflict(f"This posting was already used for `{capture.job_id}`.")

    if data.recapture:
        out = attach_posting(space, ctx, data.recapture, capture, replace=data.replace_posting)
        out["recaptured"] = True
        out["next"] = [f"The posting for {data.recapture} was updated; its application fields were left as they were."]
        return out

    # Values: the preview's fields, checked again against the posting; then the owner's own values.
    if data.fields is None:
        fields = posting_fields.extract(capture).as_list()
    else:
        posting_fields.check(capture, data.fields)
        fields = data.fields
    values: dict[str, str | None] = {f.name: f.value for f in fields}
    values.update(data.overrides)

    reg = space.repo.load_registry()
    created: dict[str, str] = {}

    # Company: the owner's choice, else the preview's suggestion; a new entry only when asked (R11.5).
    wanted = (data.company or values.get("company") or "").strip()
    if not wanted:
        raise ValidationFailed("Which company is this job at? Pass `company` (an existing slug, or a name with create_missing).")
    company = next((c for c in reg.companies if c.slug == wanted), None)
    if company is None:
        scored = score_matches(reg.companies, wanted, threshold=SAME_THRESHOLD)
        if scored:
            company = scored[0][1]
        elif data.create_missing:
            taken = {c.slug for c in space.repo.load_registry(include_archived=True).companies}
            company = Company(slug=_new_slug(wanted, taken), name=wanted)
            reg.companies.append(company)
            created["company"] = company.slug
        else:
            near = match_company(space, wanted).candidates
            hint = (" Did you mean " + ", ".join(f"`{c['slug']}` ({c['name']})" for c in near) + "?") if near else ""
            raise NotFound(f"No company in your registry matches `{wanted}`.{hint} Pick one, or add it with create_missing.")

    # Department: optional. One named in the posting is used only if it matches; an explicit choice must resolve.
    dept_slug, note = None, None
    wanted_dept = (data.department or values.get("department") or "").strip()
    if wanted_dept:
        dept = next((d for d in company.departments if d.slug == wanted_dept), None)
        if dept is None:
            scored = score_matches(company.departments, wanted_dept, threshold=SAME_THRESHOLD)
            dept = scored[0][1] if scored else None
        if dept is not None:
            dept_slug = dept.slug
        elif data.create_missing:
            dept = Department(slug=_new_slug(wanted_dept, {d.slug for d in company.departments}), name=wanted_dept)
            company.departments.append(dept)
            dept_slug = dept.slug
            created["department"] = dept.slug
        elif data.department:
            raise NotFound(f"No department under `{company.slug}` matches `{wanted_dept}`. Pick one, or add it with create_missing.")
        else:
            note = f"The posting names the department `{wanted_dept}`, which isn't in your registry, so none was set."

    duplicates = find_duplicates(space, values.get("posting_url"), company.slug, values.get("external_id"))
    if duplicates and not data.allow_duplicate:
        first = duplicates[0]
        raise Conflict(f"You're already tracking this posting as `{first['job_id']}` ({', '.join(first['reasons'])}). "
                       f"Use recapture `{first['job_id']}` to save this posting for that job, or allow_duplicate to start a new one.")

    entered = today_for(space)
    job_id = build_job_id(entered, company.slug, dept_slug, set(space.repo.job_ids()))
    app_fields = {k: v for k, v in values.items() if k in APPLICATION_FIELDS and v not in (None, "")}
    try:
        application = Application(job_id=job_id, company=company.slug, department=dept_slug, **app_fields,
                                  events=[Event(date=entered, type="stage_change", stage="interested",
                                                notes="Started tracking (job intake)")])
    except PydanticValidationError as exc:
        raise ValidationFailed("Some values can't be used for the application.",
                               [Problem(e["msg"], field=".".join(str(x) for x in e["loc"])) for e in exc.errors()]) from None
    problems = validate_registry(reg) if created else []
    problems += [p for p in validate_application(application, reg, f"jobs/{job_id}/application.yaml") if p.severity == "error"]
    if problems:
        raise ValidationFailed("Can't create the job.", problems)

    with space.repo.transaction():  # all or nothing (R13.20)
        if created:
            space.repo.replace_registry(reg, ctx.actor)
        space.repo.save_application(application, "interested", ctx.actor)
        capture = _store_posting(space, ctx, job_id, capture, None)
    if created:
        space.changed("registry")
    space.changed("job_created", job_id)
    space.changed("posting", job_id)

    nxt = [f"Add why you want this job as `interest_notes` in jobs/{job_id}/application.yaml (letters use only that for motivation).",
           f"Ask your AI agent for a profile and fit report for {job_id}, or run: rb brief {job_id}"]
    return {"job_id": job_id, "folder": f"jobs/{job_id}/", "capture_id": capture.id, "created": created,
            "fields": {k: v for k, v in values.items() if v not in (None, "")}, "note": note, "next": nxt}


def _new_slug(name: str, taken: set[str]) -> str:
    from ..slug import is_slug, slugify

    slug = slugify(name)
    if not is_slug(slug):
        raise ValidationFailed(f"Can't make a slug from `{name}`. Add it to organizations.yaml first.")
    if slug in taken:
        raise Conflict(f"`{slug}` is already in your registry (it may be archived). Use that entry.")
    return slug
