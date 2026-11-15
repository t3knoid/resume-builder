"""Job intake (PRD section 8.13). Operations are added in later steps; this module holds the shared
pieces they use.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from ..fetch import FetchedPage, FetchFailed, FetchLimits
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
