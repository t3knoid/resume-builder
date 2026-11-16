"""Request context, permissions and policies (PRD section 9.2).

Every service operation declares a permission. Authorization happens in exactly one place
(`authorize`), called by the operation registry before any handler runs. Interfaces never make
permission decisions; they only say who is calling.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import AbstractSet, Mapping, Protocol

from .errors import PermissionDenied

PERMISSIONS = frozenset({
    "content:read", "content:propose", "content:confirm",
    "profile:read", "profile:write",
    "resume:render",
    "letter:read", "letter:propose", "letter:approve",
    "snapshot:read", "snapshot:regenerate", "snapshot:pin", "snapshot:delete",
    "organization:read", "organization:write", "organization:merge",
    "application:read", "application:propose", "application:write", "event:append", "event:propose",
    "site:publish",
    "space:init", "space:check", "space:sync", "space:delete",
    "data:backup", "data:restore",
    "token:issue",
    "report:read",
})

READ_PERMISSIONS = frozenset(p for p in PERMISSIONS if p.endswith(":read")) | {"snapshot:regenerate", "space:check"}

# The tenant every local user space belongs to. A host that serves several tenants sets its own
# tenant id on each request context; the policy refuses any access across tenants (B8).
LOCAL_TENANT = "local"

# Role -> permissions. Roles are configuration, not code paths (B8). These are the core defaults;
# each App copies them into its registry, where plugins can add permissions and grant them.
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": PERMISSIONS,
    "agent": frozenset({
        "content:read", "content:propose",
        "profile:read", "profile:write",
        "letter:read", "letter:propose",
        "snapshot:read", "snapshot:regenerate",
        "organization:read",
        "application:read", "application:propose", "event:propose",
        "report:read",
    }),
    "system": frozenset({"space:init", "space:check", "space:sync"}),
    "viewer": READ_PERMISSIONS,
}


@dataclass(frozen=True)
class RequestContext:
    """Who is acting, with which roles, in which user space of which tenant (B1).

    `actor` names who is acting: `owner` (the person the user space belongs to), `agent` (an AI
    tool, B7), `system` (bootstrap and the sync watcher) or `anonymous` (the read-only website).
    `user_id` is the user space the call acts in; locally that is the folder name under the data
    root. `tenant_id` is the tenant that user space belongs to; locally always `LOCAL_TENANT`.
    Every permission check reads from this object and nothing else.
    """

    actor: str
    roles: tuple[str, ...]
    user_id: str
    channel: str = "cli"  # cli | api | yaml-sync | bootstrap | web | mcp | mcp-http
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    tenant_id: str = LOCAL_TENANT

    @property
    def user(self) -> str:
        """Short alias for `user_id`."""
        return self.user_id

    @classmethod
    def owner(cls, user: str, channel: str = "cli", tenant_id: str = LOCAL_TENANT) -> "RequestContext":
        return cls(actor="owner", roles=("owner",), user_id=user, channel=channel, tenant_id=tenant_id)

    @classmethod
    def agent(cls, user: str, channel: str = "api", tenant_id: str = LOCAL_TENANT) -> "RequestContext":
        return cls(actor="agent", roles=("agent",), user_id=user, channel=channel, tenant_id=tenant_id)

    @classmethod
    def system(cls, user: str, tenant_id: str = LOCAL_TENANT) -> "RequestContext":
        return cls(actor="system", roles=("system",), user_id=user, channel="bootstrap", tenant_id=tenant_id)

    @classmethod
    def anonymous(cls, user: str, tenant_id: str = LOCAL_TENANT) -> "RequestContext":
        return cls(actor="anonymous", roles=("viewer",), user_id=user, channel="web", tenant_id=tenant_id)

    def with_channel(self, channel: str) -> "RequestContext":
        return replace(self, channel=channel)


RoleTable = Mapping[str, AbstractSet[str]]


class Policy(Protocol):
    """Decides whether `ctx` may use `permission` in the target user space (B3).

    `roles` is the role table of the App's registry (core roles plus any plugin grants).
    """

    def allows(self, ctx: RequestContext, permission: str, target_user: str, *,
               tenant_id: str = LOCAL_TENANT, roles: RoleTable | None = None) -> bool: ...


class RolePolicy:
    """The default policy: roles grant permissions, only inside the actor's own user space and tenant (B3, B8)."""

    def __init__(self, role_permissions: RoleTable | None = None):
        self.role_permissions = role_permissions

    def allows(self, ctx: RequestContext, permission: str, target_user: str, *,
               tenant_id: str = LOCAL_TENANT, roles: RoleTable | None = None) -> bool:
        if ctx.tenant_id != tenant_id or ctx.user_id != target_user:
            return False
        table = self.role_permissions if self.role_permissions is not None else (roles if roles is not None else ROLE_PERMISSIONS)
        return any(permission in table.get(r, frozenset()) for r in ctx.roles)


class DenyAllPolicy:
    def allows(self, ctx, permission, target_user, **_) -> bool:
        return False


_policy: Policy = RolePolicy()


def set_policy(policy: Policy) -> None:
    global _policy
    _policy = policy


def get_policy() -> Policy:
    return _policy


def authorize(ctx: RequestContext, permission: str, target_user: str | None = None, *,
              known: AbstractSet[str] = PERMISSIONS, roles: RoleTable | None = None,
              tenant_id: str = LOCAL_TENANT, policy: Policy | None = None) -> None:
    """The one permission check (B3). `known` and `roles` come from the App's registry."""
    if permission not in known:
        raise PermissionDenied(f"Unknown permission `{permission}`.")
    if not (policy or _policy).allows(ctx, permission, target_user or ctx.user_id, tenant_id=tenant_id, roles=roles):
        raise PermissionDenied(
            f"`{ctx.actor}` is not allowed to do this (needs `{permission}`)."
            + (" The AI agent can only propose drafts; the owner confirms." if ctx.actor == "agent" else "")
        )
