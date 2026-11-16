"""The operation registry (PRD A5, B2).

Each service operation is defined once with a permission and typed input. The HTTP API and the MCP
tools are generated from the registry, and the CLI calls the same operations. Nothing can be done
in only one interface, and nothing runs without a permission check.

Core operations register into the module-level `REGISTRY` when `resume_builder.services` is
imported. Each `App` works on its own copy, to which plugins add operations, permissions and role
grants (see `plugins.py`), so one host's plugins never leak into another App in the same process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from pydantic import BaseModel

from .security import LOCAL_TENANT, PERMISSIONS, ROLE_PERMISSIONS, RequestContext, authorize

_PERMISSION = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")


@dataclass(frozen=True)
class Operation:
    name: str
    permission: str
    input_model: type[BaseModel]
    handler: Callable[..., Any]
    summary: str
    writes: bool


class Registry:
    """Operations by name, the known permissions, and which roles hold which permissions.

    Behaves like a read-only mapping of operation name to `Operation`.
    """

    def __init__(self, permissions=PERMISSIONS, role_permissions=ROLE_PERMISSIONS):
        self._ops: dict[str, Operation] = {}
        self.permissions: set[str] = set(permissions)
        self.role_permissions: dict[str, set[str]] = {r: set(p) for r, p in role_permissions.items()}

    # ------------------------------------------------------------ registration
    def add_permission(self, permission: str, roles: tuple[str, ...] | list[str] = ("owner",)) -> None:
        """Declare a new permission (`area:action`) and grant it to `roles` (owner by default)."""
        if not _PERMISSION.match(permission):
            raise ValueError(f"permission `{permission}` must look like `area:action` (lowercase)")
        self.permissions.add(permission)
        self.grant(*roles, permission=permission)

    def grant(self, *roles: str, permission: str) -> None:
        if permission not in self.permissions:
            raise ValueError(f"unknown permission `{permission}`; declare it with add_permission first")
        for role in roles:
            self.role_permissions.setdefault(role, set()).add(permission)

    def add(self, op: Operation) -> Operation:
        if op.permission not in self.permissions:
            raise ValueError(f"operation {op.name} declares unknown permission {op.permission}")
        if op.name in self._ops:
            raise ValueError(f"duplicate operation {op.name}")
        self._ops[op.name] = op
        return op

    def operation(self, name: str, permission: str, input_model: type[BaseModel], summary: str, writes: bool = False):
        """Decorator: register `fn(space, ctx, data)` as an operation."""
        def decorator(fn):
            self.add(Operation(name, permission, input_model, fn, summary, writes))
            return fn
        return decorator

    def copy(self) -> "Registry":
        new = Registry(self.permissions, self.role_permissions)
        new._ops = dict(self._ops)
        return new

    # ------------------------------------------------------------ queries
    def for_role(self, role: str) -> list[Operation]:
        """Operations the role may run, sorted by name (used for MCP tools)."""
        allowed = self.role_permissions.get(role, set())
        return sorted((op for op in self._ops.values() if op.permission in allowed), key=lambda o: o.name)

    def get(self, name: str) -> Operation | None:
        return self._ops.get(name)

    def __getitem__(self, name: str) -> Operation:
        return self._ops[name]

    def __contains__(self, name: object) -> bool:
        return name in self._ops

    def __iter__(self) -> Iterator[str]:
        return iter(self._ops)

    def __len__(self) -> int:
        return len(self._ops)

    def values(self):
        return self._ops.values()

    def items(self):
        return self._ops.items()


REGISTRY = Registry()


def core_registry() -> Registry:
    """The core registry with every built-in operation loaded (services and the file adapter)."""
    from . import services, sync_ops  # noqa: F401  (importing registers the operations)

    return REGISTRY


def operation(name: str, permission: str, input_model: type[BaseModel], summary: str, writes: bool = False):
    """Register a core operation (used by `resume_builder.services`)."""
    return REGISTRY.operation(name, permission, input_model, summary, writes)


def invoke(app, ctx: RequestContext, name: str, payload: dict | BaseModel | None = None) -> Any:
    """Authorize, validate input, run, and audit. The only way to run an operation."""
    from .errors import NotFound

    registry: Registry = getattr(app, "registry", REGISTRY)
    op = registry.get(name)
    if op is None:
        raise NotFound(f"Unknown operation `{name}`.")
    authorize(ctx, op.permission, known=registry.permissions, roles=registry.role_permissions,
              tenant_id=getattr(app, "tenant_id", LOCAL_TENANT), policy=getattr(app, "policy", None))
    if isinstance(payload, BaseModel):
        data = payload
    else:
        data = op.input_model.model_validate(payload or {})
    space = app.space(ctx.user_id)
    if ctx.actor == "agent":  # "last seen" for the agent status (issue #32): reads aren't audited
        from .services.agent_status import note_seen
        note_seen(space, ctx.channel)
    try:
        result = op.handler(space, ctx, data)
    except Exception as exc:  # audit failures of writes too (B6)
        if op.writes:
            space.audit(ctx, name, target=_target(data), result=f"failed: {type(exc).__name__}")
        raise
    if op.writes and not getattr(space, "destroyed", False):  # a deleted space audited itself first
        space.audit(ctx, name, target=_target(data), result="ok")
    return result


def _target(data: BaseModel) -> str:
    for key in ("job_id", "snapshot_id", "slug", "id", "kind"):
        val = getattr(data, key, None)
        if val:
            return str(val)
    return ""
