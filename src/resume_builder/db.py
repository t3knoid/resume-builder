"""SQLite connection, migrations, per-user sync lock, dump and load (PRD R7.1–R7.4, R7.5a, DK6).

SQL lives only in this module and repositories.py, always parameterized (A2).
"""

from __future__ import annotations

import contextlib
import fcntl
import sqlite3
import tempfile
import threading
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


_nesting = threading.local()  # per thread: id(conn) -> how many transactions are open on it


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection):
    """All or nothing. Nested calls in the same thread become savepoints inside the outer transaction.

    So an operation's own transaction can run inside a caller's, which commits or rolls back the
    whole (the sync adapter's dry run, R7.7).
    """
    depths = _nesting.__dict__.setdefault("depths", {})
    depth = depths.get(id(conn), 0)
    if depth == 0:
        begin, commit, rollback = "BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"
    else:
        sp = f"rb_sp_{depth}"
        begin, commit, rollback = f"SAVEPOINT {sp}", f"RELEASE {sp}", f"ROLLBACK TO {sp}; RELEASE {sp}"
    conn.execute(begin)
    depths[id(conn)] = depth + 1
    try:
        yield conn
    except BaseException:
        depths[id(conn)] = depth
        for stmt in rollback.split("; "):
            conn.execute(stmt)
        raise
    else:
        depths[id(conn)] = depth
        conn.execute(commit)
    finally:
        if depths.get(id(conn)) == 0:
            depths.pop(id(conn), None)


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
        # A migration that rebuilds a table other tables refer to (SQLite's documented table rebuild)
        # opts in with a first line `-- rb:foreign-keys-off`. Foreign keys can only be switched off
        # outside a transaction, so it happens here; before committing, foreign_key_check must find
        # nothing, or the whole migration rolls back. They are always switched back on.
        fk_off = sql.lstrip().startswith(FK_OFF_MARKER)
        if fk_off:
            conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with transaction(conn):
                for statement in _split_sql(sql):
                    conn.execute(statement)
                if fk_off:
                    broken = conn.execute("PRAGMA foreign_key_check").fetchall()
                    if broken:
                        raise sqlite3.IntegrityError(
                            f"migration {version} would leave broken references: {[tuple(r) for r in broken[:5]]}")
                conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)", (version, now_iso()))
        finally:
            if fk_off:
                conn.execute("PRAGMA foreign_keys = ON")
        applied.append(version)
    return applied


FK_OFF_MARKER = "-- rb:foreign-keys-off"


def _split_sql(sql: str) -> list[str]:
    """Split a migration into statements. Uses SQLite's own completeness check, so a `;` inside a
    trigger body (BEGIN ... END) or a string doesn't end the statement early."""
    statements, buf = [], []
    for line in sql.splitlines():
        if not buf and line.strip().startswith("--"):
            continue
        buf.append(line)
        chunk = "\n".join(buf).strip()
        if sqlite3.complete_statement(chunk):
            statements.append(chunk)
            buf = []
    tail = "\n".join(buf).strip()
    if tail and not all(ln.strip().startswith("--") or not ln.strip() for ln in buf):
        statements.append(tail)
    return statements


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
