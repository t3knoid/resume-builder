"""Job intake (PRD section 8.13). Operations are added in later steps; this module holds the shared
pieces they use.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .. import posting_fields, postingtext
from ..adapters import MAX_FETCHES, AdapterCapture, adapter_for, host_matches
from ..clock import now_iso
from ..errors import Conflict, NotFound, ValidationFailed
from ..fetch import FetchedPage, FetchFailed, FetchLimits
from ..models import ExtractedField, PostingCapture
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
    text = postingtext.posting_document(capture.markdown, source_label(capture), capture.captured_at[:10],
                                        notes=postingtext.notes_of(row["posting_text"]))
    with space.repo.transaction():
        if capture.id is None:
            capture = space.repo.add_posting_capture(capture.model_copy(update={"job_id": job_id}), ctx.actor)
        elif capture.job_id is None:
            space.repo.attach_posting_capture(capture.id, job_id)
            capture = capture.model_copy(update={"job_id": job_id})
        space.repo.set_posting(job_id, text)
        space.audit(ctx, "posting:capture", target=job_id, result=f"capture {capture.id}")
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
