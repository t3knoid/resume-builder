"""The app factory: the one way to build the FastAPI app (HTTP API plus the tracker website).

`rb serve` uses it, and so can an outside host:

    from resume_builder.web.app import create_app

    api = create_app(plugins=[my_plugin], template_dirs=["my_templates"])

Nothing is built at import time. See docs/ARCHITECTURE.md for every extension point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request

from ..config import Settings
from ..space import App, build_app


def create_app(app: App | None = None, *, settings: Settings | None = None, plugins: tuple | list = (),
               routers: tuple | list = (), template_dirs: tuple | list[str | Path] = (),
               document_template_dirs: tuple | list[str | Path] = (), load_entry_points: bool | None = None,
               **app_kwargs: Any) -> FastAPI:
    """Build the FastAPI app.

    - `app`: an existing App to serve. Without one, an App is built with `build_app(settings, ...)`,
      the same path the CLI and the MCP server use; `app_kwargs` (for example `policy`,
      `tenant_id`, `storage_factory`, `file_store_factory`, `secrets`) go to it.
    - `plugins`: applied to the App (operations, permissions, routers, template directories).
    - `routers`: extra FastAPI routers, included after plugin routers.
    - `template_dirs`: website template directories searched before the packaged ones, so a host
      can replace `base.html` or any page without editing core.
    - `document_template_dirs`: extra resume or letter template roots (new templates or versions).
    """
    from ..api import create_api

    if app is None:
        app = build_app(settings, load_entry_points=load_entry_points, plugins=plugins, template_dirs=template_dirs,
                        document_template_dirs=document_template_dirs, **app_kwargs)
    else:
        if settings is not None or app_kwargs:
            raise TypeError("pass settings and App options either to create_app or to the App, not both")
        for plugin in plugins:
            app.use(plugin)
        for d in reversed(list(template_dirs)):
            app.extensions.add_template_dir(d)
        for d in reversed(list(document_template_dirs)):
            app.extensions.add_document_templates(d)
    return create_api(app, routers=routers)


def get_app(request: Request) -> App:
    """FastAPI dependency for plugin routers: the App behind this web app."""
    return request.app.state.rb_app
