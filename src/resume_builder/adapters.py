"""Site adapters for job intake (PRD R13.16).

A site adapter knows one site's page format, for example an applicant tracking system that hosts many
employers' career pages. A plugin registers it with `ext.add_site_adapter(adapter)`. When the user
starts an intake from a URL on one of the adapter's domains, core calls the adapter instead of the
generic path.

An adapter can't bypass the intake checks:

- It fetches only through the `fetch` function core hands it. That function is the App's safe
  fetcher with the user's limits and the audit (R13.5–R13.8, R13.27), restricted to the adapter's
  own domains and to `MAX_FETCHES` requests. The page it returns must be one it got from `fetch`.
- Markdown it returns must keep the page's wording (R13.11); otherwise core converts the page itself.
- Every field it returns goes through the same passage check as any other value (R13.15). A field
  that fails is dropped and reported.

An adapter is plain code in a plugin package the host installed; these checks keep honest adapters
on the rails, they don't sandbox hostile code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol, runtime_checkable
from urllib.parse import urlsplit

from .fetch import FetchedPage
from .models import ExtractedField

MAX_FETCHES = 2

Fetch = Callable[[str], FetchedPage]


@dataclass
class AdapterCapture:
    page: FetchedPage                     # must be a page returned by the `fetch` the adapter was given
    markdown: str | None = None           # None: core converts the page (postingtext.convert)
    fields: list[ExtractedField] = field(default_factory=list)


@runtime_checkable
class SiteAdapter(Protocol):
    name: str
    domains: tuple[str, ...]  # "jobs.example-ats.com" matches that host and its subdomains

    def capture(self, url: str, fetch: Fetch) -> AdapterCapture: ...


def host_matches(host: str, domain: str) -> bool:
    host, domain = host.lower().rstrip("."), domain.lower().strip(".")
    return host == domain or host.endswith("." + domain)


def adapter_for(adapters: list[SiteAdapter], url: str) -> SiteAdapter | None:
    """The adapter for a URL: the one with the most specific matching domain (first registered wins a tie)."""
    host = urlsplit(url).hostname or ""
    best, best_len = None, -1
    for a in adapters:
        for d in a.domains:
            if host_matches(host, d) and len(d) > best_len:
                best, best_len = a, len(d)
    return best


def validate_adapter(adapter) -> None:
    if not isinstance(adapter, SiteAdapter):
        raise TypeError(f"site adapter {adapter!r} needs `name`, `domains` and `capture(url, fetch)`")
    if not adapter.name or not isinstance(adapter.domains, (tuple, list)) or not adapter.domains:
        raise TypeError(f"site adapter {adapter!r} needs a name and at least one domain")
    for d in adapter.domains:
        if not isinstance(d, str) or "." not in d.strip(".") or "/" in d or ":" in d:
            raise TypeError(f"site adapter {adapter.name}: `{d}` is not a domain name")
