"""Safe one-shot fetching of a job posting URL (PRD R13.5–R13.8).

One GET of the URL the user gave, started only by an explicit intake request. No cookies,
credentials or user data are sent. Every host, including every redirect target, is resolved and
its addresses checked; the connection then goes to the checked address itself, so a DNS answer
can't change between the check and the connection. Redirects are followed here (at most
`max_redirects`), never by the HTTP library. The body is read in chunks and refused as soon as it
passes the size limit.

`PostingFetcher` is the seam: the App takes a `fetcher`, so tests use a local stub and never make a
real network request (R13.29). Standard library only.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.parse import urljoin, urlsplit

from . import __version__
from .clock import now_iso
from .errors import AppError

USER_AGENT = f"resume-builder/{__version__} (job posting intake; one request per user action)"
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
CONTENT_TYPES = {"text/html": "text/html", "application/xhtml+xml": "text/html", "text/plain": "text/plain"}
MAX_JSON_DEPTH = 64  # JSON nested deeper than this is refused before parsing
PASTE_FALLBACK = ("Instead, copy the posting text from your browser into a file in your intake folder, "
                  "for example intake/<name>.txt, and run: rb new-job --file intake/<name>.txt <link>")


class FetchFailed(AppError):
    """A fetch that didn't produce a usable page. `reason` is a short code for the audit log."""

    status_code = 422

    def __init__(self, reason: str, message: str):
        super().__init__(f"{message} {PASTE_FALLBACK}")
        self.reason = reason


@dataclass(frozen=True)
class FetchLimits:
    timeout_seconds: float = 20
    max_bytes: int = 2_000_000
    max_redirects: int = 5
    json: bool = False  # a site adapter asked for a JSON API response; nothing else is accepted then (D31)


@dataclass(frozen=True)
class FetchedPage:
    url: str            # the URL that was asked for
    final_url: str      # after redirects
    content_type: str   # text/html | text/plain | application/json (only when a site adapter asked for JSON)
    text: str
    fetched_at: str


class PostingFetcher(Protocol):
    def fetch(self, url: str, limits: FetchLimits) -> FetchedPage: ...


def is_public_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Only globally routable unicast addresses. Refuses loopback, private, link-local (including
    cloud metadata), shared, reserved, multicast and unspecified addresses, and IPv6 forms that
    embed an IPv4 address that would be refused."""
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return is_public_address(ip.ipv4_mapped)
        if ip.sixtofour:
            return is_public_address(ip.sixtofour)
        if ip.teredo:
            return False
    return ip.is_global and not ip.is_multicast


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)  # certificate checked for the name


class HttpPostingFetcher:
    """The real fetcher. `address_allowed` exists so tests can point it at a local stub server."""

    def __init__(self, address_allowed: Callable[[ipaddress.IPv4Address | ipaddress.IPv6Address], bool] = is_public_address,
                 resolve: Callable[[str, int], list[str]] = _resolve):
        self.address_allowed = address_allowed
        self.resolve = resolve

    def fetch(self, url: str, limits: FetchLimits) -> FetchedPage:
        deadline = time.monotonic() + limits.timeout_seconds
        current = url
        for _hop in range(limits.max_redirects + 1):
            status, headers, body = self._get_once(current, limits, deadline)
            if status in REDIRECT_STATUSES:
                location = headers.get("location")
                if not location:
                    raise FetchFailed("bad-redirect", "The site sent a redirect without a destination.")
                current = urljoin(current, location)
                continue
            if status in (401, 403, 407):
                raise FetchFailed("login-or-blocked", f"The site refused the request (HTTP {status}). It may need a login or block automated requests.")
            if status == 404 or status == 410:
                raise FetchFailed("not-found", f"The page wasn't found (HTTP {status}). The posting may have been taken down.")
            if status == 429:
                raise FetchFailed("rate-limited", "The site asked to slow down (HTTP 429).")
            if status != 200:
                raise FetchFailed("http-error", f"The site answered with HTTP {status}.")
            content_type = self._content_type(headers, limits)
            text = _decode(body, headers.get("content-type", ""), content_type)
            if content_type == "application/json":
                check_json(text)
            return FetchedPage(url=url, final_url=current, content_type=content_type, text=text, fetched_at=now_iso())
        raise FetchFailed("too-many-redirects", f"The page redirected more than {limits.max_redirects} times.")

    # ------------------------------------------------------------ one request
    def _get_once(self, url: str, limits: FetchLimits, deadline: float) -> tuple[int, http.client.HTTPMessage, bytes]:
        parts = urlsplit(url)
        scheme = (parts.scheme or "").lower()
        if scheme not in ("http", "https"):
            raise FetchFailed("bad-scheme", f"Only http and https links can be fetched, not `{scheme or url}`.")
        if not parts.hostname:
            raise FetchFailed("bad-url", "That doesn't look like a web address.")
        if parts.username or parts.password:
            raise FetchFailed("bad-url", "Links with a user name or password in them can't be fetched.")
        try:
            port = parts.port or (443 if scheme == "https" else 80)
        except ValueError:
            raise FetchFailed("bad-url", "The link has an invalid port number.") from None
        host = parts.hostname
        address = self._checked_address(host, port)
        timeout = self._remaining(deadline, limits)
        cls = _PinnedHTTPSConnection if scheme == "https" else _PinnedHTTPConnection
        conn = cls(host, address, port, timeout)
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            conn.request("GET", path, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json" if limits.json else "text/html,application/xhtml+xml,text/plain;q=0.9",
                "Accept-Encoding": "identity",
                "Connection": "close",
            })
            resp = conn.getresponse()
            if resp.status in REDIRECT_STATUSES or resp.status != 200:
                return resp.status, resp.headers, b""
            self._content_type(resp.headers, limits)  # refuse before reading the body
            declared = resp.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limits.max_bytes:
                raise self._too_big(limits)
            body = bytearray()
            while True:
                # Each read waits at most the socket timeout set at connect; the overall deadline is
                # checked between reads, so a slow drip can't keep the fetch going.
                self._remaining(deadline, limits)
                chunk = resp.read(65536)
                if not chunk:
                    break
                body += chunk
                if len(body) > limits.max_bytes:
                    raise self._too_big(limits)
            return resp.status, resp.headers, bytes(body)
        except FetchFailed:
            raise
        except (socket.timeout, TimeoutError):
            raise FetchFailed("timeout", f"The site didn't answer within {limits.timeout_seconds:g} seconds.") from None
        except ssl.SSLError:
            raise FetchFailed("tls-error", "The site's secure connection couldn't be verified.") from None
        except (OSError, http.client.HTTPException):
            raise FetchFailed("connection-error", "Couldn't connect to the site.") from None
        finally:
            conn.close()

    def _checked_address(self, host: str, port: int) -> str:
        try:
            literal = ipaddress.ip_address(host)
        except ValueError:
            literal = None
        if literal is not None:
            addresses = [str(literal)]
        else:
            try:
                addresses = self.resolve(host, port)
            except (socket.gaierror, UnicodeError, OSError):
                raise FetchFailed("dns-error", f"Couldn't find the site `{host}`.") from None
        if not addresses:
            raise FetchFailed("dns-error", f"Couldn't find the site `{host}`.")
        # Every address the name resolves to must be allowed, not just the first one.
        for a in addresses:
            if not self.address_allowed(ipaddress.ip_address(a.split("%", 1)[0])):
                raise FetchFailed("address-refused", f"`{host}` points to a private or internal address, which intake never fetches.")
        return addresses[0]

    @staticmethod
    def _content_type(headers, limits: FetchLimits) -> str:
        raw = (headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        is_json = raw == "application/json" or (raw.startswith("application/") and raw.endswith("+json"))
        if limits.json:
            if not is_json:
                raise FetchFailed("content-type", f"The site adapter asked for JSON, but the site sent `{raw or 'unknown'}`.")
            return "application/json"
        if raw not in CONTENT_TYPES:
            raise FetchFailed("content-type", f"The link is not a web page or text (it is `{raw or 'unknown'}`).")
        return CONTENT_TYPES[raw]

    @staticmethod
    def _remaining(deadline: float, limits: FetchLimits) -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise FetchFailed("timeout", f"The site didn't answer within {limits.timeout_seconds:g} seconds.")
        return left

    @staticmethod
    def _too_big(limits: FetchLimits) -> FetchFailed:
        return FetchFailed("too-large", f"The page is larger than the {limits.max_bytes:,}-byte limit (`intake.max_bytes` in user.yaml).")


_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9._-]+)""", re.IGNORECASE)


def _decode(body: bytes, content_type_header: str, content_type: str) -> str:
    """Decode with the declared charset (header, then the HTML meta tag), falling back to UTF-8.
    Undecodable bytes become U+FFFD rather than failing the fetch."""
    charset = None
    m = re.search(r"charset\s*=\s*\"?([A-Za-z0-9._-]+)", content_type_header, re.IGNORECASE)
    if m:
        charset = m.group(1)
    elif content_type == "text/html":
        mm = _META_CHARSET.search(body[:4096])
        if mm:
            charset = mm.group(1).decode("ascii", "ignore")
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def check_json(text: str, max_depth: int = MAX_JSON_DEPTH) -> None:
    """Refuse JSON nested deeper than `max_depth` (checked by scanning, before any parsing, so it
    can't exhaust the parser), then refuse invalid JSON. The size limit applied while reading."""
    depth, in_string, escaped = 0, False, False
    for ch in text:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "[{":
            depth += 1
            if depth > max_depth:
                raise FetchFailed("json-too-deep", f"The site's JSON is nested more than {max_depth} levels deep.")
        elif ch in "]}":
            depth -= 1
    try:
        json.loads(text)
    except ValueError as exc:
        raise FetchFailed("invalid-json", f"The site's JSON isn't valid ({exc.msg}, line {exc.lineno}).") from None
