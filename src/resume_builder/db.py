"""SQLite connection, migrations, per-user sync lock, dump and load (PRD R7.1–R7.4, R7.5a, DK6).

SQL lives only in this module and repositories.py, always parameterized (A2).
"""

from __future__ import annotations

import contextlib
import fcntl
import sqlite3
import tempfile
from pathlib import Path

from .clock import now_iso, now_iso_precise  # noqa: F401  (re-exported)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone()
    if not row:
        return 0
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0


def pending_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> list[tuple[int, Path]]:
    have = current_version(conn)
    out = []
    for f in sorted(migrations_dir.glob("*.sql")):
        version = int(f.name.split("_", 1)[0])
        if version > have:
            out.append((version, f))
    return out


def migrate(conn: sqlite3.Connection, migrations_dir: Path, backup_fn=None) -> list[int]:
    """Apply pending migrations. Back up first if the database already holds data (R7.4)."""
    pending = pending_migrations(conn, migrations_dir)
    if not pending:
        return []
    if current_version(conn) > 0 and backup_fn:
        backup_fn("pre-migration")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = []
    for version, f in pending:
        sql = f.read_text(encoding="utf-8")
        with transaction(conn):
            for statement in _split_sql(sql):
                conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, now_iso()))
        applied.append(version)
    return applied


def _split_sql(sql: str) -> list[str]:
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


@contextlib.contextmanager
def file_lock(path: Path):
    """Per-user lock so only one sync runs at a time, across containers on the same volume (R7.5a)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def dump(conn: sqlite3.Connection) -> bytes:
    """A consistent copy of the whole database, safe while the app runs (SQLite online backup, DK6)."""
    with tempfile.TemporaryDirectory() as tmp:
        target_path = Path(tmp) / "dump.db"
        target = sqlite3.connect(target_path)
        try:
            conn.backup(target)
        finally:
            target.close()
        return target_path.read_bytes()


def load(conn: sqlite3.Connection, data: bytes) -> None:
    """Replace the database behind `conn` with a copy made by `dump`."""
    with tempfile.TemporaryDirectory() as tmp:
        source_path = Path(tmp) / "load.db"
        source_path.write_bytes(data)
        source = sqlite3.connect(source_path)
        try:
            source.backup(conn)
        finally:
            source.close()
