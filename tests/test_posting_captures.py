"""Captured job postings: append-only storage, migration and backups (PRD R13.10, D29)."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from resume_builder import db, storage
from resume_builder.errors import Conflict, NotFound
from resume_builder.models import PostingCapture, content_hash

from .conftest import JOB

MIGRATIONS = Path(db.__file__).parent / "migrations"
HTML = "<html><body>\n  <h1>Platform Engineer</h1>\n  <p>Northwind Labs builds tools.  </p>\n</body></html>\n"


def capture(**kw) -> PostingCapture:
    base = dict(source_kind="url", source_url="https://jobs.example.com/123", content_type="text/html",
                raw_content=HTML, markdown="# Platform Engineer\n\nNorthwind Labs builds tools.\n",
                captured_at="2026-11-15T10:00:00Z")
    return PostingCapture(**{**base, **kw})


# ---------------------------------------------------------------- storing and reading

def test_add_sets_id_hash_and_actor_and_keeps_text_verbatim(sample):
    stored = sample.repo.add_posting_capture(capture(job_id=JOB), "owner")
    assert stored.id and stored.created_by == "owner" and stored.created_at
    assert stored.content_hash == content_hash(HTML)
    back = sample.repo.get_posting_capture(stored.id)
    assert back == stored
    assert back.raw_content == HTML  # leading spaces and trailing whitespace kept


def test_hash_mismatch_and_saving_twice_are_refused(sample):
    with pytest.raises(Conflict):
        sample.repo.add_posting_capture(capture(content_hash="0" * 64), "owner")
    stored = sample.repo.add_posting_capture(capture(), "owner")
    with pytest.raises(Conflict):
        sample.repo.add_posting_capture(stored, "owner")


def test_latest_and_list_keep_every_capture(sample):
    first = sample.repo.add_posting_capture(capture(job_id=JOB, captured_at="2026-11-10T09:00:00Z"), "owner")
    second = sample.repo.add_posting_capture(
        capture(job_id=JOB, raw_content=HTML + "<p>Closing date added.</p>", captured_at="2026-11-15T09:00:00Z"), "owner")
    assert sample.repo.latest_posting_capture(JOB).id == second.id
    rows = sample.repo.list_posting_captures(JOB)
    assert [r["id"] for r in rows] == [first.id, second.id]
    assert all("raw_content" not in r and "markdown" not in r for r in rows)
    assert sample.repo.latest_posting_capture("2026-01-01-nobody") is None


def test_source_must_be_named(sample):
    with pytest.raises(ValidationError):
        capture(source_url=None)
    with pytest.raises(ValidationError):
        capture(source_kind="file", source_url=None)
    with pytest.raises(ValidationError):
        capture(content_type="application/pdf")
    ok = sample.repo.add_posting_capture(capture(source_kind="file", source_url=None, source_path="intake/job.txt",
                                                 content_type="text/plain", raw_content="pasted text"), "owner")
    assert ok.source_path == "intake/job.txt"


# ---------------------------------------------------------------- linking to a job

def test_an_unassigned_capture_is_linked_once(sample):
    stored = sample.repo.add_posting_capture(capture(), "agent")
    assert sample.repo.list_posting_captures(JOB) == []
    sample.repo.attach_posting_capture(stored.id, JOB)
    assert sample.repo.latest_posting_capture(JOB).id == stored.id
    with pytest.raises(Conflict):
        sample.repo.attach_posting_capture(stored.id, JOB)
    with pytest.raises(NotFound):
        sample.repo.attach_posting_capture(999_999, JOB)


def test_linking_to_an_unknown_job_is_refused(sample):
    stored = sample.repo.add_posting_capture(capture(), "owner")
    with pytest.raises(sqlite3.IntegrityError):
        sample.repo.attach_posting_capture(stored.id, "2026-01-01-nobody")


# ---------------------------------------------------------------- append-only

def test_storage_offers_no_way_to_edit_or_delete_a_capture():
    names = [n for n in storage.method_names(storage.IntakeStore) if "capture" in n]
    assert names and not [n for n in names if n.startswith(("update", "delete", "set", "replace", "put"))]


def test_schema_refuses_edits_and_deletes(sample):
    stored = sample.repo.add_posting_capture(capture(), "owner")
    conn = sample.repo.conn
    for sql in ("UPDATE posting_captures SET raw_content = 'changed' WHERE id = ?",
                "UPDATE posting_captures SET markdown = 'changed' WHERE id = ?",
                "DELETE FROM posting_captures WHERE id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql, (stored.id,))
    sample.repo.attach_posting_capture(stored.id, JOB)
    with pytest.raises(sqlite3.IntegrityError):  # moving it to another job, even with raw SQL
        conn.execute("UPDATE posting_captures SET job_id = NULL WHERE id = ?", (stored.id,))
    assert sample.repo.get_posting_capture(stored.id).raw_content == HTML


def test_schema_checks_the_source(sample):
    with pytest.raises(sqlite3.IntegrityError):
        sample.repo.conn.execute(
            "INSERT INTO posting_captures (source_kind, content_type, raw_content, markdown, content_hash, captured_at, created_by, created_at) "
            "VALUES ('url', 'text/html', 'x', 'x', ?, 'now', 'owner', 'now')", (content_hash("x"),))


# ---------------------------------------------------------------- backups and migration

def test_backups_include_captures(sample, tmp_path):
    stored = sample.repo.add_posting_capture(capture(job_id=JOB), "owner")
    copy = tmp_path / "copy.db"
    copy.write_bytes(sample.repo.dump())
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("SELECT raw_content FROM posting_captures WHERE id = ?", (stored.id,)).fetchone()[0] == HTML
    finally:
        conn.close()


def test_migration_upgrades_an_existing_database_without_losing_data(tmp_path):
    only_first = tmp_path / "v1"
    only_first.mkdir()
    shutil.copy(MIGRATIONS / "0001_initial.sql", only_first)
    conn = db.connect(tmp_path / "app.db")
    try:
        assert db.migrate(conn, only_first) == [1]
        conn.execute("INSERT INTO meta (key, value) VALUES ('kept', 'yes')")
        backups = []
        assert 2 in db.migrate(conn, MIGRATIONS, backup_fn=backups.append)
        assert backups == ["pre-migration"]
        assert conn.execute("SELECT value FROM meta WHERE key = 'kept'").fetchone()[0] == "yes"
        triggers = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert {"posting_captures_no_delete", "posting_captures_no_edit"} <= triggers
    finally:
        conn.close()


def test_sql_splitter_keeps_trigger_bodies_whole():
    sql = (MIGRATIONS / "0002_posting_captures.sql").read_text(encoding="utf-8")
    statements = db._split_sql(sql)
    assert len(statements) == 4
    assert statements[2].startswith("CREATE TRIGGER posting_captures_no_delete") and statements[2].endswith("END;")
    first = db._split_sql((MIGRATIONS / "0001_initial.sql").read_text(encoding="utf-8"))
    assert all(sqlite3.complete_statement(s) for s in first)
    assert sum(s.startswith("CREATE TABLE") for s in first) == 17
