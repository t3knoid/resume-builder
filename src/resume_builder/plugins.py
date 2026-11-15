"""Plugins: how an outside package extends resume-builder without patching core files.

A plugin is any object with a `register(ext)` method, or a plain callable taking `ext`. It gets an
`Extensions` object and can:

- add operations (`ext.operation(...)`), which then appear in the HTTP API, the CLI's `rb call`,
  and the MCP tools of every role allowed to run them, all behind the usual permission check;
- declare new permissions and grant them to roles (`ext.add_permission`, `ext.grant`);
- add FastAPI routers to the web app (`ext.add_router`);
- add website template directories that override the packaged ones (`ext.add_template_dir`);
- add document template roots with new resume or letter templates (`ext.add_document_templates`);
- add site adapters that read one site's job posting pages (`ext.add_site_adapter`, PRD R13.16).

Plugins are passed explicitly (`App(plugins=[...])`, `create_app(plugins=[...])`) or discovered
from installed packages through the `resume_builder.plugins` entry point group:

    [project.entry-points."resume_builder.plugins"]
    my_plugin = "my_package.plugin"          # a module or object with register(ext)

See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .operations import Registry

ENTRY_POINT_GROUP = "resume_builder.plugins"
log = logging.getLogger("resume_builder")


@dataclass
class Extensions:
    """What plugins and hosts can add to one App."""

    registry: Registry
    routers: list[Any] = field(default_factory=list)
    template_dirs: list[Path] = field(default_factory=list)
    document_template_dirs: list[Path] = field(default_factory=list)
    site_adapters: list[Any] = field(default_factory=list)
    loaded: list[str] = field(default_factory=list)

    # ------------------------------------------------------------ operations and permissions
    def operation(self, name: str, permission: str, input_model: type[BaseModel], summary: str, writes: bool = False):
        """Decorator registering `fn(space, ctx, data)` as an operation of this App."""
        return self.registry.operation(name, permission, input_model, summary, writes)

    def add_permission(self, permission: str, roles: tuple[str, ...] | list[str] = ("owner",)) -> None:
        self.registry.add_permission(permission, roles)

    def grant(self, *roles: str, permission: str) -> None:
        self.registry.grant(*roles, permission=permission)

    # ------------------------------------------------------------ web
    def add_router(self, router: Any) -> None:
        """A FastAPI `APIRouter`, included by `create_app`."""
        self.routers.append(router)

    def add_template_dir(self, path: str | Path) -> None:
        """A website template directory, searched before the packaged one (later additions first)."""
        self.template_dirs.insert(0, Path(path))

    def add_document_templates(self, path: str | Path) -> None:
        """A root holding `<family>/<name>/<version>/` document templates (resume or letter).

        It can add new templates or new versions. It cannot replace a released template version
        that already exists in the package: snapshots must regenerate byte for byte (R12.7).
        """
        self.document_template_dirs.insert(0, Path(path))

    # ------------------------------------------------------------ job intake
    def add_site_adapter(self, adapter: Any) -> None:
        """A `adapters.SiteAdapter` that reads one site's posting pages (PRD R13.16)."""
        from .adapters import validate_adapter

        validate_adapter(adapter)
        self.site_adapters.append(adapter)


Plugin = Any  # an object with register(ext), or a callable taking ext


def apply_plugin(ext: Extensions, plugin: Plugin, name: str | None = None) -> None:
    register = getattr(plugin, "register", None)
    if callable(register):
        register(ext)
    elif callable(plugin):
        plugin(ext)
    else:
        raise TypeError(f"plugin {name or plugin!r} has no register(ext) and is not callable")
    ext.loaded.append(name or getattr(plugin, "__name__", type(plugin).__name__))


def discover_plugins(group: str = ENTRY_POINT_GROUP) -> list[tuple[str, Plugin]]:
    """Installed plugins, in a stable order (by entry point name)."""
    found = []
    for ep in sorted(entry_points(group=group), key=lambda e: e.name):
        try:
            found.append((ep.name, ep.load()))
        except Exception:
            log.exception("could not load plugin %s (%s)", ep.name, ep.value)
            raise
    return found
