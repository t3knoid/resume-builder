"""The server: bootstrap, then the website/API plus a background sync watcher (PRD R7.5, DK14)."""

from __future__ import annotations

import logging
import threading
import time

from . import sync as sync_mod
from .space import App

log = logging.getLogger("resume_builder")


def watch(app: App, stop: threading.Event) -> None:
    """Poll user folders and sync changed files. Polling works across Docker bind mounts on every OS."""
    last_daily = 0.0
    while not stop.wait(app.settings.sync_poll_seconds):
        users, _ = app.discover_users()
        for user in users:
            try:
                space = app.space(user)
                with space._lock:
                    report = sync_mod.run_sync(space, app.context("system", user))
                    if time.time() - last_daily > 3600:
                        space.daily_backup_if_due()
                for r in report.results:
                    log.info("sync %s: %s %s", user, r.rel, r.result)
            except Exception:  # never let the watcher die
                log.exception("sync failed for %s", user)
        if time.time() - last_daily > 3600:
            last_daily = time.time()


def serve(app: App) -> int:
    import uvicorn

    from .web.app import create_app
    from .cli import print_bootstrap
    from .sync_ops import bootstrap

    logging.basicConfig(level=app.settings.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    print_bootstrap(bootstrap(app))
    stop = threading.Event()
    t = threading.Thread(target=watch, args=(app, stop), daemon=True, name="sync-watcher")
    t.start()
    import os
    print(f"\nTracker running. Open http://localhost:{os.environ.get('RB_HOST_PORT', app.settings.port)}")
    try:
        uvicorn.run(create_app(app), host="0.0.0.0", port=app.settings.port, log_level=app.settings.log_level)
    finally:
        stop.set()
    return 0
