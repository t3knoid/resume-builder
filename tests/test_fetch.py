"""Safe one-shot fetching of posting URLs (PRD R13.5–R13.9, R13.27, R13.29).

Every request goes to a local stub server. Nothing here touches the real network.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from resume_builder.fetch import FetchedPage, FetchFailed, FetchLimits, HttpPostingFetcher, is_public_address
from resume_builder.services.intake import fetch_posting

PAGE = "<html><head><meta charset='utf-8'><title>Platform Engineer</title></head><body><h1>Platform Engineer – Northwind Labs</h1></body></html>"


class Stub(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes = b"", ctype: str | None = "text/html; charset=utf-8", length: bool = True, **headers):
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        if length:
            self.send_header("Content-Length", str(len(body)))
        for k, v in headers.items():
            self.send_header(k.replace("_", "-"), v)
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_GET(self):
        Stub.seen.append({"path": self.path, "headers": dict(self.headers)})
        p = self.path
        if p == "/job":
            self._send(200, PAGE.encode())
        elif p == "/text":
            self._send(200, b"Platform Engineer\nRemote", ctype="text/plain")
        elif p == "/latin1":
            self._send(200, "<p>Café</p>".encode("latin-1"), ctype="text/html; charset=iso-8859-1")
        elif p == "/moved":
            self._send(302, Location="/job")
        elif p == "/loop":
            self._send(302, Location="/loop")
        elif p.startswith("/hop"):
            n = int(p[4:])
            self._send(302, Location=f"/hop{n + 1}" if n < 5 else "/job")
        elif p == "/to-private":
            self._send(302, Location="http://10.0.0.1/job")
        elif p == "/to-metadata":
            self._send(302, Location="http://169.254.169.254/latest/meta-data/")
        elif p == "/to-file":
            self._send(302, Location="file:///etc/passwd")
        elif p == "/big":
            self._send(200, b"x" * 50_000)
        elif p == "/big-unannounced":
            self._send(200, b"x" * 50_000, length=False)
        elif p == "/pdf":
            self._send(200, b"%PDF-1.7", ctype="application/pdf")
        elif p == "/slow":
            time.sleep(1.5)
            self._send(200, PAGE.encode())
        elif p == "/login":
            self._send(403, b"Sign in")
        elif p == "/gone":
            self._send(404, b"")
        else:
            self._send(500, b"")


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


def loopback_only(ip) -> bool:
    """Lets the tests reach the stub on 127.0.0.1 while still refusing every other internal address."""
    return ip.is_loopback or is_public_address(ip)


@pytest.fixture
def fetcher():
    return HttpPostingFetcher(address_allowed=loopback_only)


LIMITS = FetchLimits(timeout_seconds=5, max_bytes=20_000)


def reason(exc_info) -> str:
    return exc_info.value.reason


# ---------------------------------------------------------------- what gets through

def test_fetches_a_page_and_follows_a_redirect(server, fetcher):
    page = fetcher.fetch(f"{server}/moved", LIMITS)
    assert page.content_type == "text/html"
    assert "Northwind Labs" in page.text and "–" in page.text
    assert page.url.endswith("/moved") and page.final_url.endswith("/job")
    assert page.fetched_at


def test_plain_text_and_declared_charsets(server, fetcher):
    assert fetcher.fetch(f"{server}/text", LIMITS).content_type == "text/plain"
    assert "Café" in fetcher.fetch(f"{server}/latin1", LIMITS).text


def test_sends_no_cookies_and_a_generic_user_agent(server, fetcher):
    Stub.seen.clear()
    fetcher.fetch(f"{server}/job", LIMITS)
    headers = {k.lower(): v for k, v in Stub.seen[-1]["headers"].items()}
    assert "cookie" not in headers and "authorization" not in headers
    assert headers["user-agent"].startswith("resume-builder/")
    assert "alex" not in headers["user-agent"].lower()
    assert len(Stub.seen) == 1  # one GET, nothing else


def test_five_redirects_are_allowed(server, fetcher):
    assert fetcher.fetch(f"{server}/hop1", LIMITS).final_url.endswith("/job")


# ---------------------------------------------------------------- what is refused

def test_internal_addresses_are_refused_by_default(server):
    with pytest.raises(FetchFailed) as e:
        HttpPostingFetcher().fetch(f"{server}/job", LIMITS)
    assert reason(e) == "address-refused"


@pytest.mark.parametrize("path", ["/to-private", "/to-metadata"])
def test_redirects_to_internal_addresses_are_refused(server, fetcher, path):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{server}{path}", LIMITS)
    assert reason(e) == "address-refused"


def test_redirect_to_another_scheme_is_refused(server, fetcher):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{server}/to-file", LIMITS)
    assert reason(e) == "bad-scheme"


@pytest.mark.parametrize("answers", [["10.1.2.3"], ["93.184.216.34", "10.0.0.1"], ["::1"], ["::ffff:192.168.1.1"]])
def test_a_name_resolving_to_any_internal_address_is_refused(answers):
    f = HttpPostingFetcher(resolve=lambda host, port: answers)
    with pytest.raises(FetchFailed) as e:
        f.fetch("https://jobs.example.com/123", LIMITS)
    assert reason(e) == "address-refused"


def test_redirect_loop_is_refused(server, fetcher):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{server}/loop", LIMITS)
    assert reason(e) == "too-many-redirects"


@pytest.mark.parametrize("path", ["/big", "/big-unannounced"])
def test_oversize_pages_are_refused(server, fetcher, path):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{server}{path}", LIMITS)
    assert reason(e) == "too-large"
    assert "intake.max_bytes" in str(e.value)


def test_wrong_content_type_is_refused(server, fetcher):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{server}/pdf", LIMITS)
    assert reason(e) == "content-type"


def test_timeout(server, fetcher):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{server}/slow", FetchLimits(timeout_seconds=0.5, max_bytes=20_000))
    assert reason(e) == "timeout"


@pytest.mark.parametrize("path,why", [("/login", "login-or-blocked"), ("/gone", "not-found")])
def test_http_errors_explain_themselves(server, fetcher, path, why):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{server}{path}", LIMITS)
    assert reason(e) == why


@pytest.mark.parametrize("url,why", [
    ("ftp://jobs.example.com/1", "bad-scheme"),
    ("file:///etc/passwd", "bad-scheme"),
    ("https://user:pw@jobs.example.com/1", "bad-url"),
    ("https:///nohost", "bad-url"),
    ("http://127.0.0.1:99999/", "bad-url"),
])
def test_bad_urls_are_refused_before_any_connection(url, why):
    with pytest.raises(FetchFailed) as e:
        HttpPostingFetcher(resolve=lambda *a: pytest.fail("must not resolve")).fetch(url, LIMITS)
    assert reason(e) == why


def test_every_failure_shows_the_paste_fallback(server, fetcher):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{server}/login", LIMITS)
    assert "rb new-job --file intake/<name>.txt <link>" in str(e.value)


@pytest.mark.parametrize("ip,public", [
    ("93.184.216.34", True), ("2606:4700::1111", True),
    ("127.0.0.1", False), ("10.0.0.1", False), ("172.16.0.1", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False),
    ("::1", False), ("fc00::1", False), ("fe80::1", False), ("::ffff:127.0.0.1", False), ("2002:a00:1::", False),
])
def test_public_address_rule(ip, public):
    assert is_public_address(ipaddress.ip_address(ip)) is public


# ---------------------------------------------------------------- the service helper: settings and audit

class RecordingFetcher:
    def __init__(self, fail: FetchFailed | None = None):
        self.calls, self.fail = [], fail

    def fetch(self, url, limits):
        self.calls.append((url, limits))
        if self.fail:
            raise self.fail
        return FetchedPage(url=url, final_url=url, content_type="text/html", text=PAGE, fetched_at="2026-11-15T10:00:00Z")


def _fetch_audits(space):
    return [e for e in space.repo.audit_entries() if e["operation"] == "posting:fetch"]


def test_fetch_uses_user_limits_and_audits_the_host_only(sample, owner):
    stub = RecordingFetcher()
    sample.app.fetcher = stub
    page = fetch_posting(sample, owner, "https://jobs.example.com/openings/123?ref=secret-token")
    assert page.text == PAGE
    assert stub.calls[0][1] == FetchLimits(timeout_seconds=20, max_bytes=2_000_000)
    entry = _fetch_audits(sample)[0]
    assert entry["target"] == "jobs.example.com" and entry["result"] == "ok" and entry["actor"] == "owner"
    assert "openings" not in entry["target"] and "secret" not in entry["target"]


def test_failed_fetch_is_audited_with_its_reason(sample, agent):
    sample.app.fetcher = RecordingFetcher(fail=FetchFailed("timeout", "slow"))
    with pytest.raises(FetchFailed):
        fetch_posting(sample, agent, "https://jobs.example.com/1")
    entry = _fetch_audits(sample)[0]
    assert entry["result"] == "failed: timeout" and entry["actor"] == "agent"


def test_fetch_can_be_turned_off(sample, owner):
    stub = RecordingFetcher()
    sample.app.fetcher = stub
    s = sample.user_settings
    sample.repo.put_settings(s.model_copy(update={"intake": s.intake.model_copy(update={"fetch": False})}), "owner")
    with pytest.raises(FetchFailed) as e:
        fetch_posting(sample, owner, "https://jobs.example.com/1")
    assert reason(e) == "fetch-disabled" and "rb new-job --file" in str(e.value)
    assert stub.calls == []
    assert _fetch_audits(sample)[0]["result"].startswith("refused")


def test_the_app_takes_a_fetcher(roots):
    from resume_builder.config import load_settings
    from resume_builder.space import App

    stub = RecordingFetcher()
    assert App(load_settings(), fetcher=stub).fetcher is stub
    assert isinstance(App(load_settings()).fetcher, HttpPostingFetcher)
