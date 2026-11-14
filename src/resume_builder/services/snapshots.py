"""Snapshot listing, protection, limits, "as of" lookup and diffs (PRD 8.12)."""

from __future__ import annotations

import difflib
import json
from datetime import datetime

from pydantic import BaseModel, Field

from ..errors import Conflict, NotFound, ValidationFailed
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace


def protected_ids(space: UserSpace) -> set[str]:
    """Pinned, sent with an application, or the current public site (R12.10)."""
    ids = space.repo.pinned_or_sent_snapshot_ids()
    current_public = space.repo.current_public_snapshot_id()
    if current_public:
        ids.add(current_public)
    # The current approved version of each letter or answer, so its id never dangles.
    ids |= space.repo.approved_document_snapshot_ids()
    for job_id in space.repo.job_ids():
        app = space.repo.get_application(job_id)
        sub = app.submission
        ids |= {x for x in [sub.resume_snapshot, sub.cover_letter_snapshot, *sub.answer_snapshots] if x}
        for ev in app.events:
            ids |= set(ev.documents)
    return ids


def mark_sent_from_application(space: UserSpace, job_id: str) -> None:
    app = space.repo.get_application(job_id)
    sub = app.submission
    sent = {x for x in [sub.resume_snapshot, sub.cover_letter_snapshot, *sub.answer_snapshots] if x}
    for ev in app.events:
        sent |= set(ev.documents)
    with space.repo.transaction():
        for sid in sent:
            row = space.repo.get_snapshot(sid)
            if row and not row["preview"] and row["reason"] != "sent":
                space.repo.set_snapshot_reason(sid, "sent")


def enforce_limits(space: UserSpace, ctx: RequestContext, job_key: str, doc_type: str) -> None:
    """Make room for one more snapshot, deleting the oldest unprotected ones (R12.9–R12.12)."""
    limits = space.user_settings.snapshots
    protected = protected_ids(space)
    same = [r for r in space.repo.list_snapshots(job_key, doc_type) if r["id"] not in protected]
    while len(same) >= limits.max_per_job_and_type:
        victim = same.pop(0)
        _delete(space, ctx, victim["id"], "limit: per job and type")
    everything = space.repo.list_snapshots()
    if len(everything) >= limits.max_total:
        unprotected = [r for r in everything if r["id"] not in protected]
        if not unprotected:
            raise Conflict(
                f"You've reached the limit of {limits.max_total} saved documents and every one is protected (sent, public or pinned). "
                "Raise `snapshots.max_total` in user.yaml or unpin some with `rb unpin <id>`. Nothing was created.")
        while len(everything) >= limits.max_total and unprotected:
            victim = unprotected.pop(0)
            _delete(space, ctx, victim["id"], "limit: total")
            everything = space.repo.list_snapshots()


def _delete(space: UserSpace, ctx: RequestContext, sid: str, why: str) -> None:
    with space.repo.transaction():
        space.repo.delete_snapshot(sid)
    space.audit(ctx, "snapshot:delete", target=sid, result=why)


def as_of(space: UserSpace, job_key: str, doc_type: str, when: str) -> str:
    try:
        moment = datetime.fromisoformat(when if "T" in when else when + "T23:59:59+00:00")
    except ValueError:
        raise ValidationFailed(f"`{when}` is not a date. Use YYYY-MM-DD.")
    rows = [r for r in space.repo.list_snapshots(job_key, doc_type) if datetime.fromisoformat(r["created_at"]) <= moment]
    if not rows:
        raise NotFound(f"No {doc_type} snapshot for `{job_key}` at or before {when}.")
    return rows[-1]["id"]


class ListIn(BaseModel):
    job_key: str | None = None
    doc_type: str | None = None


@operation("snapshot_list", "snapshot:read", ListIn, "Saved document snapshots, oldest first.")
def snapshot_list(space: UserSpace, ctx: RequestContext, data: ListIn) -> list[dict]:
    protected = protected_ids(space)
    return [{**dict(r), "protected": r["id"] in protected} for r in space.repo.list_snapshots(data.job_key, data.doc_type)]


class SnapIn(BaseModel):
    snapshot_id: str


@operation("snapshot_get", "snapshot:read", SnapIn, "A snapshot's full content.")
def snapshot_get(space: UserSpace, ctx: RequestContext, data: SnapIn) -> dict:
    row = space.repo.get_snapshot(data.snapshot_id)
    if row is None:
        raise NotFound(f"No snapshot `{data.snapshot_id}`.")
    return {"meta": {k: row[k] for k in row.keys() if k != "data"}, "content": json.loads(row["data"])}


@operation("snapshot_pin", "snapshot:pin", SnapIn, "Pin a snapshot so it's never deleted automatically.", writes=True)
def snapshot_pin(space: UserSpace, ctx: RequestContext, data: SnapIn) -> dict:
    _require(space, data.snapshot_id)
    with space.repo.transaction():
        space.repo.set_pinned(data.snapshot_id, True)
    return {"pinned": data.snapshot_id}


@operation("snapshot_unpin", "snapshot:pin", SnapIn, "Unpin a snapshot.", writes=True)
def snapshot_unpin(space: UserSpace, ctx: RequestContext, data: SnapIn) -> dict:
    _require(space, data.snapshot_id)
    with space.repo.transaction():
        space.repo.set_pinned(data.snapshot_id, False)
    return {"unpinned": data.snapshot_id}


class DeleteIn(SnapIn):
    confirm_protected: bool = Field(False, description="Required to delete a sent, public or pinned snapshot")


@operation("snapshot_delete", "snapshot:delete", DeleteIn, "Delete a snapshot (protected ones need confirmation).", writes=True)
def snapshot_delete(space: UserSpace, ctx: RequestContext, data: DeleteIn) -> dict:
    _require(space, data.snapshot_id)
    if data.snapshot_id in protected_ids(space) and not data.confirm_protected:
        raise Conflict("This snapshot is protected (sent, public or pinned). Add --confirm to delete it anyway.")
    _delete(space, ctx, data.snapshot_id, "manual")
    return {"deleted": data.snapshot_id}


class DiffIn(BaseModel):
    a: str
    b: str


@operation("snapshot_diff", "snapshot:read", DiffIn, "Text diff between two snapshots.")
def snapshot_diff(space: UserSpace, ctx: RequestContext, data: DiffIn) -> str:
    a, b = _require(space, data.a), _require(space, data.b)
    return "\n".join(difflib.unified_diff(snapshot_text(json.loads(a["data"])), snapshot_text(json.loads(b["data"])),
                                          fromfile=data.a, tofile=data.b, lineterm=""))


def snapshot_text(c: dict) -> list[str]:
    lines = [f"{c['doc_type']} for {c['job_key']} (template {c['template']['name']} v{c['template']['version']})"]
    sec = c.get("sections")
    if sec:
        lines += ["", "SUMMARY"] + sec["summary"]
        for group in ("roles", "projects"):
            for item in sec[group]:
                lines += ["", f"{item.get('title') or item.get('name')} | {item.get('company', '')} | {item.get('start')}–{item.get('end')}"]
                lines += [f"  - {b['text']}" for b in item["bullets"]]
        lines += ["", "SKILLS"] + [f"{g['name']}: {', '.join(g['items'])}" for g in sec["skills"]]
    doc = c.get("document")
    if doc:
        if doc.get("question"):
            lines += ["", doc["question"]]
        lines += [""] + doc["paragraphs"]
    return lines


def _require(space: UserSpace, sid: str):
    row = space.repo.get_snapshot(sid)
    if row is None:
        raise NotFound(f"No snapshot `{sid}`.")
    return row
