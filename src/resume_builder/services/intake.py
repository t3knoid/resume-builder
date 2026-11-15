"""Job intake (PRD section 8.13). Operations are added in later steps; this module holds the shared
pieces they use.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from .. import postingtext
from ..clock import now_iso
from ..errors import Conflict, NotFound, ValidationFailed
from ..fetch import FetchedPage, FetchFailed, FetchLimits
from ..models import PostingCapture
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
