"""The SQLite storage backend (PRD section 9.1). The only place besides db.py that contains SQL.

`SqliteStorage` implements the `storage.Storage` interface: it turns domain models into rows and
back, and returns plain dicts, never driver rows. Services reach it only through that interface.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from . import db
from .clock import now_iso
from .errors import Conflict, NotFound
from .models import (
    Application, Bullet, Certification, Company, ContentLibrary, Department, Education, Event,
    OrganizationRegistry, Profile, Project, Role, SkillGroup, SummaryPhrase, UserSettings,
)


def _j(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _one(cur: sqlite3.Cursor) -> dict | None:
    row = cur.fetchone()
    return dict(row) if row is not None else None


def _all(cur: sqlite3.Cursor) -> list[dict]:
    return [dict(r) for r in cur.fetchall()]


class SqliteStorage:
    def __init__(self, conn: sqlite3.Connection, migrations_dir: Path | None = None):
        self.conn = conn
        self.migrations_dir = migrations_dir

    @classmethod
    def open(cls, path: Path, migrations_dir: Path) -> "SqliteStorage":
        return cls(db.connect(path), migrations_dir)

    # ------------------------------------------------------------ lifecycle
    def transaction(self):
        return db.transaction(self.conn)

    def migrate(self, backup_fn: Callable[[str], Any] | None = None) -> list[int]:
        return db.migrate(self.conn, self.migrations_dir, backup_fn=backup_fn)

    def schema_version(self) -> int:
        return db.current_version(self.conn)

    def dump(self) -> bytes:
        return db.dump(self.conn)

    def load(self, data: bytes) -> None:
        db.load(self.conn, data)

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ meta
    def meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    # ------------------------------------------------------------ user settings
    def get_settings(self) -> UserSettings | None:
        row = self.conn.execute("SELECT data FROM user_settings WHERE key = 'user'").fetchone()
        return UserSettings.model_validate_json(row["data"]) if row else None

    def put_settings(self, s: UserSettings, actor: str) -> None:
        now = now_iso()
        self.conn.execute(
            "INSERT INTO user_settings (key, data, created_by, created_at, updated_by, updated_at) VALUES ('user', ?, ?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET data = excluded.data, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (s.model_dump_json(), actor, now, actor, now),
        )

    # ------------------------------------------------------------ content library
    def load_library(self, include_archived: bool = False) -> ContentLibrary:
        where = "" if include_archived else "WHERE archived = 0"
        rows = self.conn.execute(f"SELECT * FROM content_items {where} ORDER BY position").fetchall()
        by_parent: dict[str, list[dict]] = {}
        for r in rows:
            if r["kind"] == "bullet":
                by_parent.setdefault(r["parent_id"], []).append(r)

        def bullets(parent: str) -> list[Bullet]:
            return [Bullet.model_validate_json(b["data"]) for b in by_parent.get(parent, [])]

        lib = ContentLibrary()
        for r in rows:
            data = json.loads(r["data"])
            kind = r["kind"]
            if kind == "summary_phrase":
                lib.summary_phrases.append(SummaryPhrase.model_validate(data))
            elif kind == "role":
                lib.roles.append(Role.model_validate({**data, "bullets": [b.model_dump() for b in bullets(r["id"])]}))
            elif kind == "project":
                lib.projects.append(Project.model_validate({**data, "bullets": [b.model_dump() for b in bullets(r["id"])]}))
            elif kind == "education":
                lib.education.append(Education.model_validate(data))
            elif kind == "certification":
                lib.certifications.append(Certification.model_validate(data))
            elif kind == "skill_group":
                lib.skills.append(SkillGroup.model_validate(data))
        return lib

    def has_library(self) -> bool:
        return self.conn.execute("SELECT 1 FROM content_items LIMIT 1").fetchone() is not None

    def has_registry(self) -> bool:
        return self.conn.execute("SELECT 1 FROM companies LIMIT 1").fetchone() is not None

    def archived_ids(self) -> set[str]:
        return {r["id"] for r in self.conn.execute("SELECT id FROM content_items WHERE archived = 1")}

    def replace_library(self, lib: ContentLibrary, actor: str) -> None:
        """Upsert everything in `lib`; archive (never delete) anything no longer present (R7.10)."""
        now = now_iso()
        existing = {r["id"]: r["kind"] for r in self.conn.execute("SELECT id, kind FROM content_items")}
        seen: set[str] = set()
        pos = 0

        def upsert(item_id: str, kind: str, parent: str | None, status: str | None, data: dict):
            nonlocal pos
            if item_id in existing and existing[item_id] != kind:
                raise Conflict(f"Id `{item_id}` is already used for a {existing[item_id]}; ids can't change type. Pick a new id.")
            pos += 1
            seen.add(item_id)
            self.conn.execute(
                "INSERT INTO content_items (id, kind, parent_id, position, status, archived, data, created_by, created_at, updated_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET parent_id = excluded.parent_id, "
                "position = excluded.position, status = excluded.status, archived = 0, "
                "updated_by = CASE WHEN content_items.data = excluded.data AND content_items.archived = 0 THEN content_items.updated_by ELSE excluded.updated_by END, "
                "updated_at = CASE WHEN content_items.data = excluded.data AND content_items.archived = 0 THEN content_items.updated_at ELSE excluded.updated_at END, "
                "data = excluded.data",
                (item_id, kind, parent, pos, status, _j(data), actor, now, actor, now),
            )

        for s in lib.summary_phrases:
            upsert(s.id, "summary_phrase", None, s.status, s.model_dump(mode="json"))
        for group, kind in ((lib.roles, "role"), (lib.projects, "project")):
            for item in group:
                upsert(item.id, kind, None, None, item.model_dump(mode="json", exclude={"bullets"}))
                for b in item.bullets:
                    upsert(b.id, "bullet", item.id, b.status, b.model_dump(mode="json"))
        for e in lib.education:
            upsert(e.id, "education", None, e.status, e.model_dump(mode="json"))
        for c in lib.certifications:
            upsert(c.id, "certification", None, c.status, c.model_dump(mode="json"))
        for g in lib.skills:
            upsert(g.id, "skill_group", None, None, g.model_dump(mode="json"))
        for item_id in set(existing) - seen:
            self.conn.execute("UPDATE content_items SET archived = 1, updated_by = ?, updated_at = ? WHERE id = ? AND archived = 0", (actor, now, item_id))

    # ------------------------------------------------------------ organizations
    def load_registry(self, include_archived: bool = False) -> OrganizationRegistry:
        where = "" if include_archived else "WHERE archived = 0"
        companies = []
        for c in self.conn.execute(f"SELECT * FROM companies {where} ORDER BY position, name COLLATE NOCASE"):
            data = json.loads(c["data"])
            depts = [
                Department.model_validate_json(d["data"])
                for d in self.conn.execute(
                    f"SELECT data FROM departments WHERE company_slug = ? {'AND archived = 0' if not include_archived else ''} ORDER BY position, name COLLATE NOCASE",
                    (c["slug"],),
                )
            ]
            companies.append(Company.model_validate({**data, "departments": [d.model_dump() for d in depts]}))
        return OrganizationRegistry(companies=companies)

    def replace_registry(self, reg: OrganizationRegistry, actor: str) -> None:
        now = now_iso()
        seen_c: set[str] = set()
        seen_d: set[tuple[str, str]] = set()
        for ci, c in enumerate(reg.companies, start=1):
            seen_c.add(c.slug)
            self.conn.execute(
                "INSERT INTO companies (slug, name, position, archived, data, created_by, created_at, updated_by, updated_at) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?) "
                "ON CONFLICT(slug) DO UPDATE SET name = excluded.name, position = excluded.position, archived = 0, data = excluded.data, "
                "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                (c.slug, c.name, ci, _j(c.model_dump(mode="json", exclude={"departments"})), actor, now, actor, now),
            )
            for di, d in enumerate(c.departments, start=1):
                seen_d.add((c.slug, d.slug))
                self.conn.execute(
                    "INSERT INTO departments (company_slug, slug, name, position, archived, data, created_by, created_at, updated_by, updated_at) "
                    "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?) ON CONFLICT(company_slug, slug) DO UPDATE SET name = excluded.name, "
                    "position = excluded.position, archived = 0, data = excluded.data, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                    (c.slug, d.slug, d.name, di, _j(d.model_dump(mode="json")), actor, now, actor, now),
                )
        for r in self.conn.execute("SELECT slug FROM companies WHERE archived = 0").fetchall():
            if r["slug"] not in seen_c:
                self.conn.execute("UPDATE companies SET archived = 1, updated_by = ?, updated_at = ? WHERE slug = ?", (actor, now, r["slug"]))
        for r in self.conn.execute("SELECT company_slug, slug FROM departments WHERE archived = 0").fetchall():
            if (r["company_slug"], r["slug"]) not in seen_d:
                self.conn.execute(
                    "UPDATE departments SET archived = 1, updated_by = ?, updated_at = ? WHERE company_slug = ? AND slug = ?",
                    (actor, now, r["company_slug"], r["slug"]),
                )

    def reassign_company(self, from_slug: str, to_slug: str) -> int:
        cur = self.conn.execute("UPDATE applications SET company_slug = ?, department_slug = NULL WHERE company_slug = ?", (to_slug, from_slug))
        return cur.rowcount

    # ------------------------------------------------------------ applications
    def job_ids(self, include_archived: bool = True) -> list[str]:
        where = "" if include_archived else "WHERE archived = 0"
        return [r["job_id"] for r in self.conn.execute(f"SELECT job_id FROM applications {where} ORDER BY job_id")]

    def get_application(self, job_id: str) -> Application:
        row = self.conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            raise NotFound(f"No job `{job_id}`.")
        data = json.loads(row["data"])
        data["events"] = [json.loads(e["data"]) for e in self.conn.execute("SELECT data FROM events WHERE job_id = ? ORDER BY seq", (job_id,))]
        return Application.model_validate(data)

    def application_row(self, job_id: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM applications WHERE job_id = ?", (job_id,)))

    def list_application_rows(self, include_archived: bool = False) -> list[dict]:
        where = "" if include_archived else "WHERE archived = 0"
        return _all(self.conn.execute(f"SELECT * FROM applications {where} ORDER BY created_at DESC, job_id DESC"))

    def save_application(self, app: Application, stage: str, actor: str) -> None:
        """Save fixed fields and append any events beyond those already stored (append-only, R6.4)."""
        now = now_iso()
        data = app.model_dump(mode="json", exclude={"events"})
        self.conn.execute(
            "INSERT INTO applications (job_id, company_slug, department_slug, stage, archived, data, created_by, created_at, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET company_slug = excluded.company_slug, "
            "department_slug = excluded.department_slug, stage = excluded.stage, data = excluded.data, archived = 0, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (app.job_id, app.company, app.department, stage, _j(data), actor, now, actor, now),
        )
        have = self.conn.execute("SELECT COUNT(*) FROM events WHERE job_id = ?", (app.job_id,)).fetchone()[0]
        for seq, ev in enumerate(app.events, start=1):
            if seq <= have:
                continue
            self.conn.execute(
                "INSERT INTO events (job_id, seq, date, type, stage, data, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (app.job_id, seq, ev.date.isoformat(), ev.type, ev.stage, _j(ev.model_dump(mode="json")), actor, now),
            )
        self.conn.execute("DELETE FROM contacts WHERE job_id = ?", (app.job_id,))
        for i, c in enumerate(app.contacts, start=1):
            self.conn.execute(
                "INSERT INTO contacts (job_id, position, name, role, data) VALUES (?, ?, ?, ?, ?)",
                (app.job_id, i, c.name, c.role, _j(c.model_dump(mode="json"))),
            )

    def event_rows(self, job_id: str) -> list[dict]:
        return _all(self.conn.execute("SELECT * FROM events WHERE job_id = ? ORDER BY seq", (job_id,)))

    def all_events(self) -> list[dict]:
        return _all(self.conn.execute("SELECT * FROM events ORDER BY job_id, seq"))

    def set_posting(self, job_id: str, text: str | None) -> None:
        self.conn.execute("UPDATE applications SET posting_text = ? WHERE job_id = ?", (text, job_id))

    def set_archived(self, job_id: str, archived: bool, actor: str) -> None:
        self.conn.execute("UPDATE applications SET archived = ?, updated_by = ?, updated_at = ? WHERE job_id = ?", (int(archived), actor, now_iso(), job_id))

    def add_event_draft(self, job_id: str, event: Event, actor: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO event_drafts (job_id, data, created_by, created_at) VALUES (?, ?, ?, ?)",
            (job_id, _j(event.model_dump(mode="json")), actor, now_iso()),
        )
        return cur.lastrowid

    def event_drafts(self, job_id: str | None = None) -> list[dict]:
        if job_id:
            return _all(self.conn.execute("SELECT * FROM event_drafts WHERE job_id = ? ORDER BY id", (job_id,)))
        return _all(self.conn.execute("SELECT * FROM event_drafts ORDER BY id"))

    def delete_event_draft(self, draft_id: int) -> None:
        self.conn.execute("DELETE FROM event_drafts WHERE id = ?", (draft_id,))

    # ------------------------------------------------------------ profiles
    def get_profile(self, key: str) -> Profile | None:
        row = self.conn.execute("SELECT data FROM profiles WHERE profile_key = ?", (key,)).fetchone()
        return Profile.model_validate_json(row["data"]) if row else None

    def save_profile(self, key: str, profile: Profile, actor: str) -> None:
        now = now_iso()
        job_id = None if key == "public" else key
        self.conn.execute(
            "INSERT INTO profiles (profile_key, job_id, data, created_by, created_at, updated_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_key) DO UPDATE SET data = excluded.data, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (key, job_id, profile.model_dump_json(), actor, now, actor, now),
        )

    def profile_row(self, key: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM profiles WHERE profile_key = ?", (key,)))

    # ------------------------------------------------------------ documents
    def get_document(self, job_id: str, kind: str, slug: str = "") -> dict | None:
        return _one(self.conn.execute("SELECT * FROM documents WHERE job_id = ? AND kind = ? AND slug = ?", (job_id, kind, slug)))

    def list_documents(self, job_id: str) -> list[dict]:
        return _all(self.conn.execute("SELECT * FROM documents WHERE job_id = ? ORDER BY kind, slug", (job_id,)))

    def save_document(self, job_id: str, kind: str, slug: str, text: str, status: str, actor: str,
                      question: str | None = None, warnings: list | None = None, approved_snapshot: str | None = None) -> None:
        now = now_iso()
        self.conn.execute(
            "INSERT INTO documents (job_id, kind, slug, status, question, text, warnings, approved_snapshot, created_by, created_at, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(job_id, kind, slug) DO UPDATE SET status = excluded.status, "
            "question = COALESCE(excluded.question, documents.question), text = excluded.text, warnings = excluded.warnings, "
            "approved_snapshot = excluded.approved_snapshot, updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (job_id, kind, slug, status, question, text, _j(warnings or []), approved_snapshot, actor, now, actor, now),
        )

    # ------------------------------------------------------------ snapshots
    def get_snapshot(self, snapshot_id: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)))

    def insert_snapshot(self, sid: str, job_key: str, doc_type: str, preview: bool, reason: str, data: str,
                        bullet_ids: list[str], actor: str, created_at: str) -> None:
        self.conn.execute(
            "INSERT INTO snapshots (id, job_key, doc_type, preview, reason, pinned, data, created_by, created_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
            (sid, job_key, doc_type, int(preview), reason, data, actor, created_at),
        )
        for b in sorted(set(bullet_ids)):
            self.conn.execute("INSERT INTO snapshot_bullets (snapshot_id, bullet_id) VALUES (?, ?)", (sid, b))

    def list_snapshots(self, job_key: str | None = None, doc_type: str | None = None) -> list[dict]:
        sql, args = "SELECT id, job_key, doc_type, preview, reason, pinned, created_by, created_at FROM snapshots WHERE 1=1", []
        if job_key:
            sql += " AND job_key = ?"
            args.append(job_key)
        if doc_type:
            sql += " AND doc_type = ?"
            args.append(doc_type)
        return _all(self.conn.execute(sql + " ORDER BY created_at, rowid", args))

    def set_snapshot_reason(self, sid: str, reason: str) -> None:
        self.conn.execute("UPDATE snapshots SET reason = ? WHERE id = ?", (reason, sid))

    def set_pinned(self, sid: str, pinned: bool) -> None:
        self.conn.execute("UPDATE snapshots SET pinned = ? WHERE id = ?", (int(pinned), sid))

    def delete_snapshot(self, sid: str) -> None:
        self.conn.execute("DELETE FROM snapshots WHERE id = ?", (sid,))

    def count_snapshots(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]

    def pinned_or_sent_snapshot_ids(self) -> set[str]:
        return {r["id"] for r in self.conn.execute("SELECT id FROM snapshots WHERE pinned = 1 OR reason = 'sent'")}

    def current_public_snapshot_id(self) -> str | None:
        row = self.conn.execute(
            "SELECT id FROM snapshots WHERE doc_type = 'public_site' AND reason = 'public' ORDER BY created_at DESC LIMIT 1").fetchone()
        return row["id"] if row else None

    def approved_document_snapshot_ids(self) -> set[str]:
        return {r["approved_snapshot"] for r in self.conn.execute(
            "SELECT approved_snapshot FROM documents WHERE status = 'approved' AND approved_snapshot IS NOT NULL")}

    def snapshot_bullets(self, sid: str) -> list[str]:
        return [r["bullet_id"] for r in self.conn.execute("SELECT bullet_id FROM snapshot_bullets WHERE snapshot_id = ?", (sid,))]

    # ------------------------------------------------------------ audit
    def audit(self, actor: str, channel: str, request_id: str, operation: str, target: str, result: str) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (at, actor, channel, request_id, operation, target, result) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), actor, channel, request_id, operation, target, result),
        )

    def audit_entries(self, limit: int = 100) -> list[dict]:
        return _all(self.conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)))

    # ------------------------------------------------------------ sync state
    def sync_file(self, path: str) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM sync_files WHERE path = ?", (path,)))

    def sync_files(self) -> list[dict]:
        return _all(self.conn.execute("SELECT * FROM sync_files ORDER BY path"))

    def put_sync_file(self, path: str, hash_: str, content: Any, result: str, problems: list[dict], schema_version: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO sync_files (path, hash, content, schema_version, synced_at, result, problems) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(path) DO UPDATE SET hash = excluded.hash, content = excluded.content, schema_version = excluded.schema_version, "
            "synced_at = excluded.synced_at, result = excluded.result, problems = excluded.problems",
            (path, hash_, None if content is None else _j(content), schema_version, now_iso(), result, _j(problems)),
        )

    def mark_sync_problem(self, path: str, result: str, problems: list[dict]) -> None:
        """Record a failure without touching the last good hash/content."""
        row = self.sync_file(path)
        if row:
            self.conn.execute("UPDATE sync_files SET result = ?, problems = ?, synced_at = ? WHERE path = ?", (result, _j(problems), now_iso(), path))
        else:
            self.conn.execute(
                "INSERT INTO sync_files (path, hash, content, schema_version, synced_at, result, problems) VALUES (?, '', NULL, NULL, ?, ?, ?)",
                (path, now_iso(), result, _j(problems)),
            )

    def start_sync_run(self) -> int:
        return self.conn.execute("INSERT INTO sync_runs (started_at) VALUES (?)", (now_iso(),)).lastrowid

    def finish_sync_run(self, run_id: int, applied: int, failed: int, summary: dict) -> None:
        self.conn.execute(
            "UPDATE sync_runs SET finished_at = ?, applied = ?, failed = ?, summary = ? WHERE id = ?",
            (now_iso(), applied, failed, _j(summary), run_id),
        )

    def last_sync_run(self) -> dict | None:
        return _one(self.conn.execute("SELECT * FROM sync_runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"))

    # ------------------------------------------------------------ tokens
    def add_token(self, actor: str, token_hash: str) -> None:
        self.conn.execute("INSERT INTO tokens (actor, token_hash, created_at) VALUES (?, ?, ?)", (actor, token_hash, now_iso()))

    def revoke_tokens(self, actor: str) -> None:
        self.conn.execute("UPDATE tokens SET revoked = 1 WHERE actor = ?", (actor,))

    def token_actor(self, token_hash: str) -> str | None:
        row = self.conn.execute("SELECT actor FROM tokens WHERE token_hash = ? AND revoked = 0", (token_hash,)).fetchone()
        return row["actor"] if row else None

    def has_tokens(self, actor: str) -> bool:
        return self.conn.execute("SELECT 1 FROM tokens WHERE actor = ? AND revoked = 0", (actor,)).fetchone() is not None


Repo = SqliteStorage  # the earlier name
