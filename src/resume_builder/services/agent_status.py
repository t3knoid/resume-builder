"""Is an AI agent set up, and when did it last do something? (issue #32)

The app can't see the user's assistant or its settings; it sees what reaches it. So the status is
built from what each channel leaves behind:

- `last seen` per channel (mcp, api, cli), kept in the `meta` table and updated whenever the agent
  runs any operation, reads included (only writes are audited), at most once a minute;
- `agent:connect` audit entries, recorded when an MCP client connects (at most once an hour), with
  the client's name if it sends one;
- the agent token's issue date, and `api:token-replaced` audit entries when the agent keeps using a
  token that `rb token` has replaced.

Nothing here returns a token or its hash.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ..clock import now_iso
from ..operations import operation
from ..security import RequestContext
from ..space import UserSpace

CHANNELS = ("mcp", "api", "cli")
SEEN_EVERY = timedelta(minutes=1)
CONNECT_EVERY = timedelta(hours=1)
REFUSED_EVERY = timedelta(minutes=10)
RECENT = 5
QUIET_OPS = {"agent:connect", "api:token-replaced"}


def _ago(stamp: str | None, delta: timedelta) -> bool:
    """True if `stamp` is missing or older than `delta`."""
    if not stamp:
        return True
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(stamp) >= delta
    except ValueError:
        return True


def clean_client(name: object, version: object = None, limit: int = 80) -> str | None:
    """An MCP client's self-reported name and version, as plain short text (it's untrusted input)."""
    parts = [str(x) for x in (name, version) if isinstance(x, (str, int, float)) and str(x).strip()]
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", " ".join(parts)).strip()
    return text[:limit] or None


def note_seen(space: UserSpace, channel: str) -> None:
    """The agent just ran an operation on `channel` (throttled; never fails the call)."""
    if channel not in CHANNELS:
        return
    key = f"agent.last_seen.{channel}"
    try:
        if _ago(space.repo.meta_get(key), SEEN_EVERY):
            space.repo.meta_set(key, now_iso())
    except Exception:  # bookkeeping must never break the agent's real work
        pass


def note_connect(space: UserSpace, ctx: RequestContext, client: str | None) -> None:
    """An MCP client connected (`initialize`). Audited at most once an hour, with its name."""
    note_seen(space, "mcp")
    if client:
        space.repo.meta_set("agent.client.mcp", client)
    if _ago(space.repo.meta_get("agent.last_connect_audit"), CONNECT_EVERY):
        space.repo.meta_set("agent.last_connect_audit", now_iso())
        space.audit(ctx, "agent:connect", target=client or "", result="ok")


def note_replaced_token(space: UserSpace) -> None:
    """The agent used an API token that was replaced. Audited at most every 10 minutes."""
    now = now_iso()
    space.repo.meta_set("agent.last_refused.api", now)
    if _ago(space.repo.meta_get("agent.last_refused_audit"), REFUSED_EVERY):
        space.repo.meta_set("agent.last_refused_audit", now)
        space.audit(RequestContext.agent(space.user, channel="api", tenant_id=space.app.tenant_id),
                    "api:token-replaced", result="refused")


def summary(space: UserSpace) -> dict:
    repo = space.repo
    # "Last seen" is recorded from the first run with this feature; before that, the agent's audited
    # writes on each channel tell when it was last active, so an agent used earlier isn't "not set up".
    seen = {}
    for c in CHANNELS:
        stamps = [x for x in (repo.meta_get(f"agent.last_seen.{c}"),
                              repo.last_audit_at("agent", c, exclude=tuple(QUIET_OPS))) if x]
        seen[c] = max(stamps) if stamps else None
    token_issued = repo.active_token_created("agent")
    refused = repo.meta_get("agent.last_refused.api")
    last_channel, last_active = None, None
    for c, at in seen.items():
        if at and (last_active is None or at > last_active):
            last_channel, last_active = c, at
    # "connected" means an assistant reached the app directly (MCP or the HTTP API). Drafts pasted with
    # `rb propose` arrive as the agent's work on channel cli, but no assistant is wired up (#33).
    if seen["mcp"] or seen["api"]:
        state = "connected"
    elif token_issued:
        state = "token_unused"
    elif seen["cli"]:
        state = "copy_and_paste"
    else:
        state = "not_set_up"
    direct = [(at, c) for c, at in seen.items() if c in ("mcp", "api") and at]
    direct_active, direct_channel = max(direct) if direct else (None, None)
    stale_token = bool(refused and (not seen["api"] or refused > seen["api"]))
    return {"state": state, "last_active": last_active, "last_channel": last_channel, "stale_token": stale_token,
            "direct_channel": direct_channel, "direct_active": direct_active,
            "seen": seen, "token_issued": token_issued, "token_refused": refused}


class Empty(BaseModel):
    pass


@operation("agent_status", "report:read", Empty, "Whether an AI agent is set up, when it was last active, and what it did.")
def agent_status(space: UserSpace, ctx: RequestContext, data: Empty) -> dict:
    s = summary(space)
    recent = []
    for row in space.repo.audit_entries_by("agent", RECENT * 4):
        if row["operation"] in QUIET_OPS:
            continue
        target = row["target"] or ""
        recent.append({"at": row["at"], "operation": row["operation"], "channel": row["channel"], "result": row["result"],
                       "job_id": target if space.repo.application_row(target) else None})
        if len(recent) == RECENT:
            break
    hints = []
    if s["state"] == "not_set_up":
        hints.append("No AI agent has connected yet. Set one up with AI-AGENTS.md (MCP: `rb mcp`; HTTP API: "
                     "`rb token --actor agent`), then ask it to call `agent_brief`. This updates when it does.")
    elif s["state"] == "token_unused":
        hints.append("An agent token was issued but hasn't been used yet. Put it in your AI tool's settings (AI-AGENTS.md).")
    elif s["state"] == "copy_and_paste":
        hints.append("You've pasted drafts from a chat assistant with `rb propose`, but no assistant has connected directly. "
                     "To let one use resume-builder's tools itself, set it up with AI-AGENTS.md (MCP or the HTTP API).")
    if s["stale_token"]:
        hints.append("Your assistant used an API token that's no longer valid (it was replaced). "
                     "Issue a new one with `rb token --actor agent` and update your AI tool.")
    return {
        "state": s["state"],
        "last_active": s["last_active"],
        "last_channel": s["last_channel"],
        "direct_channel": s["direct_channel"],   # mcp or api: how an assistant last connected directly
        "direct_active": s["direct_active"],
        "channels": {
            "mcp": {"last_seen": s["seen"]["mcp"], "client": space.repo.meta_get("agent.client.mcp")},
            "api": {"last_seen": s["seen"]["api"], "token_issued": s["token_issued"],
                    "token_used": bool(s["token_issued"] and s["seen"]["api"] and s["seen"]["api"] >= s["token_issued"]),
                    "replaced_token_refused": s["token_refused"]},
            "cli": {"last_seen": s["seen"]["cli"]},
        },
        "recent": recent,
        "waiting": {"jobs": len(space.repo.list_intake_proposals("pending")), "events": len(space.repo.event_drafts())},
        "hints": hints,
    }
