"""User spaces: status, tokens, backups, audit and exports (PRD 8.10, B9a, DK6).

Creating, repairing and syncing the user's folder is the YAML/Markdown adapter's job: see
`resume_builder.sync_ops`.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path

from pydantic import BaseModel, Field

from ..errors import NotFound
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace


class Empty(BaseModel):
    pass


def status(space: UserSpace) -> dict:
    """The bootstrap/website summary (R10.7)."""
    s = space.user_settings
    lib = space.repo.load_library()
    examples = [i for i in lib.all_ids() if i.startswith("example-")]
    problems = [
        {"file": r["path"], "result": r["result"], "problems": json.loads(r["problems"])}
        for r in space.repo.sync_files() if r["result"] in ("failed", "conflict", "waiting")
    ]
    from .render import contact_values
    _, missing_secrets = contact_values(space, s)
    snaps = space.repo.count_snapshots()
    last = space.repo.last_sync_run()
    return {
        "last_sync": last["finished_at"] if last else None,
        "files_needing_attention": problems,
        "example_entries_left": examples,
        "missing_contact_secrets": missing_secrets,
        "snapshots": {"count": snaps, "limit": s.snapshots.max_total, "near_limit": snaps >= 0.9 * s.snapshots.max_total},
        "seed_version": space.app.settings.seed_version,
        "event_drafts_waiting": len(space.repo.event_drafts()),
        "job_proposals_waiting": len(space.repo.list_intake_proposals("pending")),
        "agent": _agent_summary(space),
    }


def _agent_summary(space: UserSpace) -> dict:
    from .agent_status import summary
    s = summary(space)
    return {"state": s["state"], "last_active": s["last_active"], "last_channel": s["last_channel"], "stale_token": s["stale_token"]}


@operation("space_status", "space:check", Empty, "Sync status, problems, example entries left, limits.")
def space_status(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    return status(space)


# ---------------------------------------------------------------- tokens (B9a)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenIn(BaseModel):
    actor: str = Field(pattern="^(owner|agent)$")


@operation("token_issue", "token:issue", TokenIn, "Issue a new API token for the owner or the agent (revokes the old one).", writes=True)
def token_issue(space: UserSpace, ctx: RequestContext, data: TokenIn) -> dict:
    token = f"rb_{data.actor}_{secrets.token_urlsafe(24)}"
    with space.repo.transaction():
        space.repo.revoke_tokens(data.actor)
        space.repo.add_token(data.actor, hash_token(token))
    return {"actor": data.actor, "token": token, "note": "Shown once. Store it in your AI tool's settings, not in your data folder."}


def actor_for_token(space: UserSpace, token: str) -> str | None:
    return space.repo.token_actor(hash_token(token))


LOGIN_CODE_MINUTES = 10


@operation("web_login_link", "token:issue", Empty,
           "A one-time code to sign in to the tracker website as the owner (valid 10 minutes, once).", writes=True)
def web_login_link(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    """Only a hash of the code is stored. The website exchanges it for a session (issue #30)."""
    import secrets
    from datetime import datetime, timedelta, timezone

    code = secrets.token_urlsafe(24)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=LOGIN_CODE_MINUTES)).replace(microsecond=0).isoformat()
    with space.repo.transaction():
        space.repo.add_login_code(hash_token(code), expires)
    return {"code": code, "expires_at": expires, "path": f"/u/{space.user}/login?code={code}"}


# ---------------------------------------------------------------- backups (DK6)

@operation("backup", "data:backup", Empty, "Back up the database now.", writes=True)
def backup_op(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    return {"backup": str(space.backup("manual"))}


class RestoreIn(BaseModel):
    file: str = Field(description="Backup file name in /backups/<user>/")


@operation("restore", "data:restore", RestoreIn, "Restore the database from a backup (backs up the current one first).", writes=True)
def restore_op(space: UserSpace, ctx: RequestContext, data: RestoreIn) -> dict:
    name = Path(data.file).name
    if name not in space.backup_names():
        raise NotFound(f"No backup `{data.file}` in {space.backups.location('')}. Run `rb backups` to list them.")
    before = space.backup("pre-restore")
    space.repo.load(space.backups.get(name))
    space.migrate()
    return {"restored": space.backups.location(name), "previous_saved_as": before,
            "next": "Run `rb sync` to re-apply any newer edits from your files."}


@operation("backups_list", "data:backup", Empty, "List database backups.")
def backups_list(space: UserSpace, ctx: RequestContext, data: Empty) -> list[str]:
    return space.backup_names()


@operation("audit_list", "report:read", Empty, "The latest audit log entries.")
def audit_list(space: UserSpace, ctx: RequestContext, data: Empty) -> list[dict]:
    return [dict(r) for r in space.repo.audit_entries(200)]


@operation("clean_output", "resume:render", Empty, "Delete exported files in /output (they can always be regenerated).", writes=True)
def clean_output(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    keys = space.output.list()
    for key in keys:
        space.output.delete(key)
    return {"deleted_files": len(keys)}
