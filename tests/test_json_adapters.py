"""Site adapters that read applicant tracking system JSON APIs (issue #29, PRD R13.7, R13.10, R13.16, D31).

The JSON API, its sites and employers are fictional. Nothing here touches the network.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from resume_builder import db, postingtext
from resume_builder.adapters import AdapterCapture
from resume_builder.config import load_settings
from resume_builder.fetch import FetchedPage, FetchFailed, FetchLimits, HttpPostingFetcher, check_json, is_public_address
from resume_builder.models import ExtractedField, PostingCapture, content_hash
from resume_builder.operations import invoke
from resume_builder.posting_fields import verify
from resume_builder.services.intake import capture_url
from resume_builder.space import App

MIGRATIONS = Path(db.__file__).parent / "migrations"
API_URL = "https://api.example-ats.com/v1/boards/fabrikam/jobs/77"
PAGE_URL = "https://jobs.example-ats.com/fabrikam/77"

POSTING = {
    "id": 77,
    "requisition_id": "FAB-3310",
    "title": "Site Reliability Engineer",
    "company": {"name": "Fabrikam Inc."},
    "location": {"name": "Austin, TX"},
    "updated_at": "2026-11-10T08:00:00Z",
    "content": "<p>Fabrikam runs the payment platform behind thousands of small businesses.</p>"
               "<ul><li>Lead incident response and write blameless post-incident reviews.</li>"
               "<li>Automate capacity planning with Terraform and Python.</li></ul>",
    "pay": "The base salary range for this role is $150,000 - $180,000 per year.",
}
GOOD_MD = ("## Site Reliability Engineer\n\n"
           "Fabrikam runs the payment platform behind thousands of small businesses.\n\n"
           "- Lead incident response and write blameless post-incident reviews.\n"
           "- Automate capacity planning with Terraform and Python.\n\n"
           "The base salary range for this role is $150,000 - $180,000 per year.\n")


# ---------------------------------------------------------------- migration 0004

def _db_at_0003(tmp_path):
    first = tmp_path / "upto3"
    first.mkdir()
    for f in ("0001_initial.sql", "0002_posting_captures.sql", "0003_intake_proposals.sql"):
        shutil.copy(MIGRATIONS / f, first)
    conn = db.connect(tmp_path / "app.db")
    assert db.migrate(conn, first) == [1, 2, 3]
    return conn, first


def _seed(conn):
    conn.execute("INSERT INTO companies (slug, name, data, created_by, created_at, updated_by, updated_at) "
                 "VALUES ('northwind-labs', 'Northwind Labs', '{}', 'owner', 't', 'owner', 't')")
    conn.execute("INSERT INTO applications (job_id, company_slug, stage, data, created_by, created_at, updated_by, updated_at) "
                 "VALUES ('2026-11-01-northwind-labs', 'northwind-labs', 'interested', '{}', 'owner', 't', 'owner', 't')")
    rows = [("2026-11-01-northwind-labs", "url", "https://careers.example.com/1", None, "text/html", "<p>one</p>", "one\n"),
            (None, "file", None, "intake/two.txt", "text/plain", "two", "two\n"),
            (None, "stdin", "https://careers.example.com/3", None, "text/plain", "three  \n", "three\n")]
    for job, kind, url, path, ctype, raw, md in rows:
        conn.execute("INSERT INTO posting_captures (job_id, source_kind, source_url, source_path, content_type, raw_content, markdown, "
                     "content_hash, captured_at, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-11-15T10:00:00+00:00', 'owner', 't')",
                     (job, kind, url, path, ctype, raw, md, content_hash(raw)))
    conn.execute("INSERT INTO intake_proposals (capture_id, status, data, note, created_by, created_at, updated_by, updated_at) "
                 "VALUES (3, 'pending', '{\"fields\": []}', 'from the agent', 'agent', 't', 'agent', 't')")


def _dump_table(conn, table):
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def test_migration_keeps_every_capture_and_proposal(tmp_path):
    conn, _ = _db_at_0003(tmp_path)
    try:
        _seed(conn)
        captures, proposals = _dump_table(conn, "posting_captures"), _dump_table(conn, "intake_proposals")
        with pytest.raises(sqlite3.IntegrityError):  # JSON isn't allowed before 0004
            conn.execute("INSERT INTO posting_captures (source_kind, source_url, content_type, raw_content, markdown, content_hash, "
                         "captured_at, created_by, created_at) VALUES ('url', 'https://x.example', 'application/json', '{}', 'x', ?, 't', 'o', 't')",
                         (content_hash("{}"),))
        backups = []
        assert db.migrate(conn, MIGRATIONS, backup_fn=backups.append) == [4] and backups == ["pre-migration"]
        assert _dump_table(conn, "posting_captures") == captures
        assert _dump_table(conn, "intake_proposals") == proposals
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1               # switched back on
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'intake_proposals'").fetchone()[0]
        assert "REFERENCES posting_captures(id)" in sql
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('index', 'trigger')")}
        assert {"posting_captures_job", "posting_captures_no_delete", "posting_captures_no_edit"} <= names
        assert not [n for n in names if "rebuilt" in n or "before" in n]
        for sql in ("UPDATE posting_captures SET raw_content = 'x' WHERE id = 1", "DELETE FROM posting_captures WHERE id = 2"):
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql)
        cur = conn.execute("INSERT INTO posting_captures (source_kind, source_url, content_type, raw_content, markdown, content_hash, "
                           "captured_at, created_by, created_at) VALUES ('url', 'https://x.example', 'application/json', '{}', 'x', ?, 't', 'o', 't')",
                           (content_hash("{}"),))
        assert cur.lastrowid == 4                                                    # ids continue after the copied rows
        with pytest.raises(sqlite3.IntegrityError):                                 # the reference is still enforced
            conn.execute("INSERT INTO intake_proposals (capture_id, status, data, created_by, created_at, updated_by, updated_at) "
                         "VALUES (999, 'pending', '{}', 'agent', 't', 'agent', 't')")
    finally:
        conn.close()


def test_a_foreign_keys_off_migration_that_breaks_references_rolls_back(tmp_path):
    conn, first = _db_at_0003(tmp_path)
    try:
        bad = tmp_path / "bad"
        shutil.copytree(first, bad)
        (bad / "0004_breaks_things.sql").write_text(
            "-- rb:foreign-keys-off\nINSERT INTO intake_proposals (capture_id, status, data, created_by, created_at, updated_by, updated_at) "
            "VALUES (999, 'pending', '{}', 'agent', 't', 'agent', 't');\n", encoding="utf-8")
        with pytest.raises(sqlite3.IntegrityError) as e:
            db.migrate(conn, bad)
        assert "broken references" in str(e.value)
        assert db.current_version(conn) == 3
        assert conn.execute("SELECT COUNT(*) FROM intake_proposals").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------- fetching JSON

class Api(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/json":
            self._send(json.dumps(POSTING).encode(), "application/json; charset=utf-8")
        elif self.path == "/ld":
            self._send(json.dumps(POSTING).encode(), "application/ld+json")
        elif self.path == "/html":
            self._send(b"<html><body><p>A web page</p></body></html>", "text/html")
        elif self.path == "/deep":
            self._send(b"[" * 100 + b"]" * 100, "application/json")
        elif self.path == "/broken":
            self._send(b'{"title": "Engineer", }', "application/json")
        elif self.path == "/big":
            self._send(json.dumps({"x": "y" * 50_000}).encode(), "application/json")


@pytest.fixture(scope="module")
def api():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def fetcher():
    return HttpPostingFetcher(address_allowed=lambda ip: ip.is_loopback or is_public_address(ip))


JSON = FetchLimits(timeout_seconds=5, max_bytes=20_000, json=True)
PAGE = FetchLimits(timeout_seconds=5, max_bytes=20_000)


def test_json_is_fetched_only_when_asked_for(api, fetcher):
    page = fetcher.fetch(f"{api}/json", JSON)
    assert page.content_type == "application/json" and json.loads(page.text)["title"] == "Site Reliability Engineer"
    assert fetcher.fetch(f"{api}/ld", JSON).content_type == "application/json"     # application/*+json
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{api}/json", PAGE)                                          # the generic path refuses JSON
    assert e.value.reason == "content-type"
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{api}/html", JSON)                                          # asked for JSON, got a page
    assert e.value.reason == "content-type"


@pytest.mark.parametrize("path,reason", [("/deep", "json-too-deep"), ("/broken", "invalid-json"), ("/big", "too-large")])
def test_unsafe_json_is_refused(api, fetcher, path, reason):
    with pytest.raises(FetchFailed) as e:
        fetcher.fetch(f"{api}{path}", JSON)
    assert e.value.reason == reason


def test_depth_check_ignores_brackets_inside_strings():
    check_json(json.dumps({"text": "[" * 500 + "{" * 500}))
    with pytest.raises(FetchFailed):
        check_json('{"a": ' * 65 + "1" + "}" * 65)


# ---------------------------------------------------------------- the wording check for JSON (block by block)

RAW = json.dumps(POSTING)


def test_blocks_from_different_values_in_any_order_pass():
    md = ("The base salary range for this role is $150,000 - $180,000 per year.\n\n"   # from "pay"
          "- Automate capacity planning with Terraform and Python.\n"                   # from "content" (HTML)
          "## Site Reliability Engineer\n")                                             # from "title"
    assert postingtext.wording_preserved(md, RAW, "application/json")
    assert postingtext.wording_preserved(GOOD_MD, RAW, "application/json")


def test_a_block_mixing_two_values_fails():
    md = "Site Reliability Engineer Fabrikam runs the payment platform.\n"
    assert not postingtext.wording_preserved(md, RAW, "application/json")


@pytest.mark.parametrize("block", [
    "- Automate planning capacity with Terraform and Python.",                    # reordered
    "- Automate capacity planning with Terraform, Kubernetes and Python.",        # added word
    "Fabrikam is a fast-growing leader in payments.",                             # reworded
])
def test_a_changed_block_fails(block):
    assert not postingtext.wording_preserved(GOOD_MD + "\n" + block + "\n", RAW, "application/json")


def test_html_inside_a_value_is_matched_as_text():
    assert postingtext.markdown_blocks("# A\n\nPara one\nstill one\n\n- x\n1. y\n") == ["A", "Para one still one", "x", "y"]
    assert postingtext.wording_preserved("- Lead incident response and write blameless post-incident reviews.",
                                         RAW, "application/json")


def test_passages_can_be_quoted_from_json_values():
    cap = PostingCapture(source_kind="url", source_url=API_URL, content_type="application/json", raw_content=RAW,
                         markdown=GOOD_MD, captured_at="2026-11-15T10:00:00+00:00")
    ok = [ExtractedField(name="external_id", value="FAB-3310", passage="FAB-3310", origin="adapter"),
          ExtractedField(name="location", value="Austin, TX", passage="Austin, TX", origin="adapter"),
          ExtractedField(name="date_posted", value="2026-11-10", passage="2026-11-10T08:00:00Z", origin="adapter")]
    assert verify(cap, ok) == []
    assert verify(cap, [ExtractedField(name="external_id", value="FAB-9999", passage="FAB-9999")])


# ---------------------------------------------------------------- an adapter reading a JSON API, end to end

class StubFetcher:
    def __init__(self):
        self.calls = []

    def fetch(self, url, limits):
        self.calls.append((url, limits.json))
        if limits.json:
            return FetchedPage(url=url, final_url=url, content_type="application/json", text=RAW,
                               fetched_at="2026-11-15T10:00:00+00:00")
        raise FetchFailed("content-type", "not a page")


class ExampleAtsApi:
    """Reads the fictional jobs.example-ats.com through its JSON API."""

    name = "example-ats-api"
    domains = ("example-ats.com",)

    def __init__(self, markdown=GOOD_MD):
        self.markdown = markdown

    def capture(self, url, fetch):
        board, job = url.rstrip("/").split("/")[-2:]
        page = fetch(f"https://api.example-ats.com/v1/boards/{board}/jobs/{job}", accept="json")
        data = json.loads(page.text)
        return AdapterCapture(page=page, markdown=self.markdown, fields=[
            ExtractedField(name="title", value=data["title"], passage=data["title"]),
            ExtractedField(name="company", value=data["company"]["name"], passage=data["company"]["name"]),
            ExtractedField(name="external_id", value=data["requisition_id"], passage=data["requisition_id"]),
            ExtractedField(name="location", value=data["location"]["name"], passage=data["location"]["name"]),
        ])


@pytest.fixture
def space_with(sample):
    apps = []

    def make(adapter):
        app = App(load_settings(), plugins=[lambda ext: ext.add_site_adapter(adapter)], fetcher=StubFetcher())
        apps.append(app)
        return app.space(sample.user)

    yield make
    for a in apps:
        a.close()


def test_adapter_reads_a_json_api_end_to_end(space_with, owner):
    space = space_with(ExampleAtsApi())
    preview = invoke(space.app, owner, "job_intake_preview", {"url": PAGE_URL})
    assert space.app.fetcher.calls == [(API_URL, True)]
    assert preview["adapter"] == "example-ats-api"
    fields = {f["name"]: (f["value"], f["origin"]) for f in preview["fields"]}
    assert fields["external_id"] == ("FAB-3310", "adapter") and fields["company"] == ("Fabrikam Inc.", "adapter")
    assert fields["salary_range"][0] == "$150,000 - $180,000 per year"               # core rules still fill the rest
    cap = space.repo.get_posting_capture(preview["capture_id"])
    assert cap.content_type == "application/json" and cap.raw_content == RAW and cap.markdown == GOOD_MD
    assert cap.source_url == PAGE_URL
    out = invoke(space.app, owner, "job_intake", {"capture_id": preview["capture_id"], "fields": preview["fields"],
                                                  "create_missing": True})
    assert space.repo.get_application(out["job_id"]).external_id == "FAB-3310"


def test_reworded_json_markdown_is_refused(space_with, owner):
    space = space_with(ExampleAtsApi(markdown="## Site Reliability Engineer\n\nFabrikam is a fast-growing payments leader.\n"))
    with pytest.raises(FetchFailed) as e:
        capture_url(space, owner, PAGE_URL)
    assert e.value.reason == "adapter-error" and "wording" in e.value.message
    assert space.repo.conn.execute("SELECT COUNT(*) FROM posting_captures").fetchone()[0] == 0


def test_json_without_markdown_is_refused(space_with, owner):
    with pytest.raises(FetchFailed) as e:
        capture_url(space_with(ExampleAtsApi(markdown=None)), owner, PAGE_URL)
    assert "no posting text" in e.value.message


def test_adapter_asks_for_an_unknown_kind(space_with, owner):
    class Odd(ExampleAtsApi):
        def capture(self, url, fetch):
            return AdapterCapture(page=fetch(url, accept="xml"))

    with pytest.raises(FetchFailed) as e:
        capture_url(space_with(Odd()), owner, PAGE_URL)
    assert e.value.reason == "adapter-error"
